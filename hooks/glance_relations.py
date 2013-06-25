#!/usr/bin/python
import sys
import os
import json

from glance_common import (
    configure_https,
    do_openstack_upgrade,
    set_or_update,
    )

from glance_utils import (
    register_configs,
    migrate_database,
    )

from charmhelpers.contrib.hahelpers.cluster_utils import (
    https,
    peer_units,
    determine_haproxy_port,
    determine_api_port,
    eligible_leader,
    is_clustered,
    )

from charmhelpers.contrib.hahelpers.utils import (
    juju_log,
    start,
    stop,
    restart,
    unit_get,
    relation_set,
    relation_ids,
    relation_list,
    install,
    do_hooks,
    relation_get,
    relation_get_dict,
    configure_source,
    )

from charmhelpers.contrib.hahelpers.haproxy_utils import (
    configure_haproxy,
    )

from charmhelpers.contrib.openstack.openstack_utils import (
    get_os_codename_package,
    get_os_codename_install_source,
    get_os_version_codename,
    save_script_rc,
    )

from charmhelpers.contrib.hahelpers.ceph_utils import (
    configure,
    )

from subprocess import (
    check_output,
    check_call,
    )

from commands import getstatusoutput

CLUSTER_RES = "res_glance_vip"

CONFIGS = register_configs()


PACKAGES = [
    "glance", "python-mysqldb", "python-swift",
    "python-keystone", "uuid", "haproxy",
    ]

SERVICES = [
    "glance-api", "glance-registry",
    ]

CHARM = "glance"
SERVICE_NAME = os.getenv('JUJU_UNIT_NAME').split('/')[0]

config = json.loads(check_output(['config-get','--format=json']))


def install_hook():
    juju_log('INFO', 'Installing glance packages')
    configure_source()

    install(*PACKAGES)

    stop(*SERVICES)

    set_or_update(key='verbose', value=True, file='api')
    set_or_update(key='debug', value=True, file='api')
    set_or_update(key='verbose', value=True, file='registry')
    set_or_update(key='debug', value=True, file='registry')

    configure_https()


def db_joined():
    relation_set(database=config['database'], username=config['database-user'],
                hostname=unit_get('private-address'))


def db_changed():
    rel = get_os_codename_package("glance-common")

    if 'shared-db' not in CONFIGS.complete_contexts():
        juju_log('INFO', 'shared-db relation incomplete. Peer not ready?')
        return

    CONFIGS.write('/etc/glance/glance-registry.conf')
    # since folsom, a db connection setting in glance-api.conf is required.
    if rel != "essex":
        CONFIGS.write('/etc/glance/glance-api.conf')

    if eligible_leader(CLUSTER_RES):
        if rel == "essex":
            (status, output) = getstatusoutput('glance-manage db_version')
            if status != 0:
                juju_log('INFO', 'Setting version_control to 0')
                check_call(["glance-manage", "version_control", "0"])

        juju_log('INFO', 'Cluster leader, performing db sync')
        migrate_database()

    restart(*SERVICES)


def image_service_joined(relation_id=None):

    if not eligible_leader("res_glance_vip"):
        return
    scheme = "http"
    if https():
        scheme = "https"
    host = unit_get('private-address')
    if is_clustered():
        host = config["vip"]

    relation_data = {
        'glance-api-server': "%s://%s:9292" % (scheme, host),
        }

    if relation_id:
        relation_data['rid'] = relation_id

    juju_log("INFO", "%s: image-service_joined: To peer glance-api-server=%s" % (CHARM, relation_data['glance-api-server']))

    relation_set(**relation_data)


def object_store_joined():
    relids = relation_ids('identity-service')

    if not relids:
        juju_log('INFO', 'Deferring swift stora configuration until ' \
                         'an identity-service relation exists')
        return

    set_or_update(key='default_store', value='swift', file='api')
    set_or_update(key='swift_store_create_container_on_put', value=True, file='api')

    for rid in relids:
        for unit in relation_list(rid=rid):
            svc_tenant = relation_get(attribute='service_tenant', rid=rid, unit=unit)
            svc_username = relation_get(attribute='service_username', rid=rid, unit=unit)
            svc_password = relation_get(attribute='service_passwod', rid=rid, unit=unit)
            auth_host = relation_get(attribute='private-address', rid=rid, unit=unit)
            port = relation_get(attribute='service_port', rid=rid, unit=unit)

            if auth_host and port:
                auth_url = "http://%s:%s/v2.0" % (auth_host, port)
            if svc_tenant and svc_username:
                value = "%s:%s" % (svc_tenant, svc_username)
                set_or_update(key='swift_store_user', value=value, file='api')
            if svc_password:
                set_or_update(key='swift_store_key', value=svc_password, file='api')
            if auth_url:
                set_or_update(key='swift_store_auth_address', value=auth_url, file='api')

    restart('glance-api')


def object_store_changed():
    pass


def ceph_joined():
    if not os.path.isdir('/etc/ceph')
        os.mkdir('/etc/ceph')
    install(['ceph-common', 'python-ceph'])


def ceph_changed():
    if 'ceph' not in CONFIGS.complete_contexts():
        juju_log('ceph relation incomplete. Peer not ready?')
        return

    if not ensure_ceph_keyring(service=SERVICE_NAME):
        juju_log('Could not create ceph keyring: peer not ready?')
        return

    CONFIGS.write('/etc/glance/glance-api.conf')
    CONFIGS.write('/etc/ceph/ceph.conf')

    set_ceph_env_variables(service=SERVICE_NAME)

    if eligible_leader(CLUSTER_RES):
        ensure_ceph_pool(service=SERVICE_NAME)

    restart('glance-api')


def ceph_changed(rid=None, unit=None):
    key = relation_get(attribute='key', rid=rid, unit=unit)
    auth = relation_get(attribute='auth', rid=rid, unit=unit)

    if None in [auth, key]:
        juju_log('INFO', 'Missing key or auth in relation')
        return

    configure(service=SERVICE_NAME, key=key, auth=auth)

    # Configure glance for ceph storage options
    set_or_update(key='default_store', value='rbd', file='api')
    set_or_update(key='rbd_store_ceph_conf', value='/etc/ceph/ceph.conf', file='api')
    set_or_update(key='rbd_store_user', value=SERVICE_NAME, file='api')
    set_or_update(key='rbd_store_pool', value='images', file='api')
    set_or_update(key='rbd_store_chunk_size', value='8', file='api')
    restart('glance-api')


def keystone_joined(relation_id=None):
    if not eligible_leader(CLUSTER_RES):
        juju_log('INFO',
                 'Deferring keystone_joined() to service leader.')
        return

    scheme = "http"
    if https():
        scheme = "https"

    host = unit_get('private-address')
    if is_clustered():
        host = config["vip"]

    url = "%s://%s:9292" % (scheme, host)

    relation_data = {
        'service': 'glance',
        'region': config['region'],
        'public_url': url,
        'admin_url': url,
        'internal_url': url,
        }

    if relation_id:
        relation_data['rid'] = relation_id

    relation_set(**relation_data)


def keystone_changed():
    if 'identity-service' not in CONFIGS.complete_contexts():
        juju_log('INFO', 'identity-service relation incomplete. Peer not ready?')
        return

    CONFIGS.write('/etc/glance/glance-api.conf')
    CONFIGS.write('/etc/glance/glance-registry.conf')

    CONFIGS.write('/etc/glance/glance-api-paste.ini')
    CONFIGS.write('/etc/glance/glance-registry-paste.ini')

    restart(*SERVICES)

    # Configure any object-store / swift relations now that we have an
    # identity-service
    if relation_ids('object-store'):
        object_store_joined()

    # possibly configure HTTPS for API and registry
    configure_https()

    # TODO: maybe this should be removed as it was added on the initial port.
    #for r_id in relation_ids('identity-service'):
    #    keystone_joined(relation_id=r_id)
    #for r_id in relation_ids('image-service'):
    #    image_service_joined(relation_id=r_id)


def config_changed():
    # Determine whether or not we should do an upgrade, based on whether or not
    # the version offered in openstack-origin is greater than what is installed.
    install_src = config["openstack-origin"]
    available = get_os_codename_install_source(install_src)
    installed = get_os_codename_package("glance-common")

    if (available and
        get_os_version_codename(available) > \
        get_os_version_codename(installed)):
        juju_log('INFO', '%s: Upgrading OpenStack release: %s -> %s' % (CHARM, installed, available))
        do_openstack_upgrade(config["openstack-origin"], ' '.join(PACKAGES))

    configure_https()

    # Update the new config files for existing relations.
    relids = relation_ids('shared-db')
    if relids:
        juju_log('INFO', '%s: Configuring database after upgrade to %s.' % (CHARM, install_src))
        for relid in relids:
            db_changed(rid=relid)

    relids = relation_ids('identity-service')
    if relids:
        juju_log('INFO', '%s: Configuring identity service after upgrade to %s' % (CHARM, install_src))
        for relid in relids:
            keystone_changed(rid=relids)

    relids = relation_ids('ceph')
    if relids:
        install('ceph-common', 'python-ceph')
        for relid in relids:
            for unit in relation_list(relid):
                ceph_changed(rid=relid, unit=unit)

    relids = relation_ids('object-store')
    if relids:
        object_store_joined()

    relids = relation_ids('image-service')
    if relids:
        for relid in relids:
            image_service_joined(relation_id=relid)

    restart(*SERVICES)

    env_vars = {'OPENSTACK_PORT_MCASTPORT': config["ha-mcastport"],
                'OPENSTACK_SERVICE_API': "glance-api",
                'OPENSTACK_SERVICE_REGISTRY': "glance-registry"}
    save_script_rc(**env_vars)


def cluster_changed():
    if not peer_units():
        juju_log('INFO', '%s: cluster_change() with no peers.' % CHARM)
        sys.exit(0)
    haproxy_port = determine_haproxy_port('9292')
    backend_port = determine_api_port('9292')
    stop('glance-api')
    configure_haproxy("glance_api:%s:%s" % (haproxy_port, backend_port))
    set_or_update(key='bind_port', value=backend_port, file='api')
    start('glance-api')

def upgrade_charm():
    cluster_changed()

def ha_relation_joined():
    corosync_bindiface = config["ha-bindiface"]
    corosync_mcastport = config["ha-mcastport"]
    vip = config["vip"]
    vip_iface = config["vip_iface"]
    vip_cidr = config["vip_cidr"]

    #if vip and vip_iface and vip_cidr and \
    #    corosync_bindiface and corosync_mcastport:

    resources = {
        'res_glance_vip': 'ocf:heartbeat:IPaddr2',
        'res_glance_haproxy': 'lsb:haproxy',
        }

    resource_params = {
        'res_glance_vip': 'params ip="%s" cidr_netmask="%s" nic="%s"' % \
                          (vip, vip_cidr, vip_iface),
        'res_glance_haproxy': 'op monitor interval="5s"',
        }

    init_services = {
        'res_glance_haproxy': 'haproxy',
        }

    clones = {
        'cl_glance_haproxy': 'res_glance_haproxy',
        }

    relation_set(init_services=init_services,
                 corosync_bindiface=corosync_bindiface,
                 corosync_mcastport=corosync_mcastport,
                 resources=resources,
                 resource_params=resource_params,
                 clones=clones)


def ha_relation_changed():
    relation_data = relation_get_dict()
    if ('clustered' in relation_data and
        eligible_leader("res_glance_vip")):
        host = config["vip"]
        if https():
            scheme = "https"
        else:
            scheme = "http"
        url = "%s://%s:9292" % (scheme, host)
        juju_log('INFO', '%s: Cluster configured, notifying other services' % CHARM)
        # Tell all related services to start using
        # the VIP
        # TODO: recommendations by adam_g
        # TODO: could be further simpllfiied by letting keystone_joined()
        # and image-service_joined() take parameters of relation_id
        # then just call keystone_joined(r_id) + image-service_joined(r_d)
        for r_id in relation_ids('identity-service'):
            relation_set(rid=r_id,
                         service="glance",
                         region=config["region"],
                         public_url=url,
                         admin_url=url,
                         internal_url=url)

        # TODO: Fix this in a better way. Maybe change 'glance-api-server'
        # to 'glance_api_server' as the first one errors as a parameter
        relation_data = {
                'rid': r_id,
                'glance-api-server': url
            }
        for r_id in relation_ids('image-service'):
            relation_set(**relation_data)
            #relation_set(rid=r_id,
            #           glance-api-server=url


hooks = {
  'install': install_hook,
  'config-changed': config_changed,
  'shared-db-relation-joined': db_joined,
  'shared-db-relation-changed': db_changed,
  'image-service-relation-joined': image_service_joined,
  'object-store-relation-joined': object_store_joined,
  'object-store-relation-changed': object_store_changed,
  'identity-service-relation-joined': keystone_joined,
  'identity-service-relation-changed': keystone_changed,
  'ceph-relation-joined': ceph_joined,
  'ceph-relation-changed': ceph_changed,
  'cluster-relation-changed': cluster_changed,
  'cluster-relation-departed': cluster_changed,
  'ha-relation-joined': ha_relation_joined,
  'ha-relation-changed': ha_relation_changed,
  'upgrade-charm': upgrade_charm,
}

do_hooks(hooks)
