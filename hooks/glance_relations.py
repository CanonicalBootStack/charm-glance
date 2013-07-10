#!/usr/bin/python
import os
import json

from glance_utils import (
    do_openstack_upgrade,
    ensure_ceph_keyring,
    ensure_ceph_pool,
    migrate_database,
    register_configs,
    restart_map,
    set_ceph_env_variables)

from charmhelpers.core.hookenv import (
    service_name,
    relation_set,
    relation_ids,
    unit_get)

from charmhelpers.core.host import (
    restart_on_change,
    apt_install,
    apt_update,
    service_stop)

from charmhelpers.contrib.hahelpers.cluster_utils import (
    eligible_leader,
    is_clustered)

from charmhelpers.contrib.hahelpers.utils import (
    juju_log,
    relation_list,
    do_hooks,
    relation_get_dict)

from charmhelpers.contrib.openstack.openstack_utils import (
    configure_installation_source,
    get_os_codename_package,
    get_os_codename_install_source,
    get_os_version_codename,
    save_script_rc,
    lsb_release)

from subprocess import (
    check_output,
    check_call)

from commands import getstatusoutput

CLUSTER_RES = "res_glance_vip"

CONFIGS = register_configs()


PACKAGES = [
    "apache2", "glance", "python-mysqldb", "python-swift",
    "python-keystone", "uuid", "haproxy",
    ]

SERVICES = [
    "glance-api", "glance-registry",
    ]

CHARM = "glance"
SERVICE_NAME = service_name()

config = json.loads(check_output(['config-get','--format=json']))


def install_hook():
    juju_log('INFO', 'Installing glance packages')

    src = config['openstack-origin']
    if (lsb_release()['DISTRIB_CODENAME'] == 'precise' and
        src == 'distro'):
        src = 'cloud:precise-folsom'

    configure_installation_source(src)

    apt_update()
    apt_install(PACKAGES)

    for service in SERVICES:
        service_stop(service)

    configure_https()


def db_joined():
    relation_set(database=config['database'], username=config['database-user'],
                hostname=unit_get('private-address'))


@restart_on_change(restart_map())
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


def image_service_joined(relation_id=None):

    if not eligible_leader(CLUSTER_RES):
        return

    scheme = "http"
    if 'https' in CONFIGS.complete_contexts():
        scheme = "https"

    host = unit_get('private-address')
    if is_clustered():
        host = config["vip"]

    relation_data = {
        'glance-api-server': "%s://%s:9292" % (scheme, host),
        }

    juju_log("INFO", "%s: image-service_joined: To peer glance-api-server=%s" % (CHARM, relation_data['glance-api-server']))

    relation_set(relation_id=relation_id, **relation_data)


@restart_on_change(restart_map())
def object_store_joined():

    if 'identity-service' not in CONFIGS.complete_contexts():
        juju_log('INFO', 'Deferring swift stora configuration until ' \
                         'an identity-service relation exists')
        return

    if 'object-store' not in CONFIGS.complete_contexts():
        juju_log('INFO', 'swift relation incomplete')
        return

    CONFIGS.write('/etc/glance/glance-api.conf')


def object_store_changed():
    pass


def ceph_joined():
    if not os.path.isdir('/etc/ceph'):
        os.mkdir('/etc/ceph')
    apt_install(['ceph-common', 'python-ceph'])


@restart_on_change(restart_map())
def ceph_changed():
    if 'ceph' not in CONFIGS.complete_contexts():
        juju_log('ERROR', 'ceph relation incomplete. Peer not ready?')
        return

    if not ensure_ceph_keyring(service=SERVICE_NAME):
        juju_log('ERROR', 'Could not create ceph keyring: peer not ready?')
        return

    CONFIGS.write('/etc/glance/glance-api.conf')
    CONFIGS.write('/etc/ceph/ceph.conf')

    set_ceph_env_variables(service=SERVICE_NAME)

    if eligible_leader(CLUSTER_RES):
        ensure_ceph_pool(service=SERVICE_NAME)


def keystone_joined(relation_id=None):
    if not eligible_leader(CLUSTER_RES):
        juju_log('INFO',
                 'Deferring keystone_joined() to service leader.')
        return

    scheme = "http"
    if 'https' in CONFIGS.complete_contexts():
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

    relation_set(relation_id=relation_id, **relation_data)


@restart_on_change(restart_map())
def keystone_changed():
    if 'identity-service' not in CONFIGS.complete_contexts():
        juju_log('INFO', 'identity-service relation incomplete. Peer not ready?')
        return

    CONFIGS.write('/etc/glance/glance-api.conf')
    CONFIGS.write('/etc/glance/glance-registry.conf')

    CONFIGS.write('/etc/glance/glance-api-paste.ini')
    CONFIGS.write('/etc/glance/glance-registry-paste.ini')

    # Configure any object-store / swift relations now that we have an
    # identity-service
    if relation_ids('object-store'):
        object_store_joined()

    # possibly configure HTTPS for API and registry
    configure_https()


@restart_on_change(restart_map())
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

        # Update the new config files for existing relations.
        for r_id in relation_ids('shared-db'):
            juju_log('INFO', '%s: Configuring database after upgrade to %s.' % (CHARM, install_src))
            db_changed()

        for r_id in relation_ids('identity-service'):
            juju_log('INFO', '%s: Configuring identity service after upgrade to %s' % (CHARM, install_src))
            keystone_changed()

        for r_id in relation_ids('ceph'):
            #for relid in relids:
            #    for unit in relation_list(relid):
            #        ceph_changed(relation_id=r_id, unit=unit)
            ceph_changed()

        for r_id in relation_ids('object-store'):
            object_store_joined()

    configure_https()

    env_vars = {'OPENSTACK_PORT_MCASTPORT': config["ha-mcastport"],
                'OPENSTACK_SERVICE_API': "glance-api",
                'OPENSTACK_SERVICE_REGISTRY': "glance-registry"}
    save_script_rc(**env_vars)


@restart_on_change(restart_map())
def cluster_changed():
    CONFIGS.write('/etc/glance/glance-api.conf')
    CONFIGS.write('/etc/haproxy/haproxy.cfg')


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
        eligible_leader(CLUSTER_RES)):
        host = config["vip"]
        scheme = "http"
        if 'https' in CONFIGS.complete_contexts():
            scheme = "https"
        url = "%s://%s:9292" % (scheme, host)
        juju_log('INFO', '%s: Cluster configured, notifying other services' % CHARM)

        for r_id in relation_ids('identity-service'):
            relation_set(relation_id=r_id,
                         service="glance",
                         region=config["region"],
                         public_url=url,
                         admin_url=url,
                         internal_url=url)

        for r_id in relation_ids('image-service'):
            relation_data = {
                'glance-api-server': url
                }
            relation_set(relation_id=rid, **relation_data)


def configure_https():
    '''
    Enables SSL API Apache config if appropriate and kicks
    identity-service and image-service with any required
    updates
    '''
    CONFIGS.write_all()
    if 'https' in CONFIGS.complete_contexts():
        cmd = ['a2ensite', 'openstack_https_frontend']
        check_call(cmd)
    else:
        cmd = ['a2dissite', 'openstack_https_frontend']
        check_call(cmd)
        return

    for r_id in relation_ids('identity-service'):
        keystone_joined(relation_id=r_id)
    for r_id in relation_ids('image-service'):
        image_service_joined(relation_id=r_id)


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
