#!/bin/bash

from lib.openstack_common import (
    get_os_codename_install_source,
    get_os_codename_package,
    configure_installation_source,
    )

from lib.utils import (
    relation_ids,
    install,
    stop,
    )

from lib.cluster_utils import (
    is_clustered,
    determine_haproxy_port,
    determine_api_port,
    )

from glance-relations import (
    db_changed,
    keystone_changed,
    ceph_changed,
    object-store_changed,
    )

CHARM = "glance"

SERVICES = "glance-api glance-registry"
PACKAGES = "glance python-mysqldb python-swift python-keystone uuid haproxy"

GLANCE_REGISTRY_CONF = "/etc/glance/glance-registry.conf"
GLANCE_REGISTRY_PASTE_INI = "/etc/glance/glance-registry-paste.ini"
GLANCE_API_CONF = "/etc/glance/glance-api.conf"
GLANCE_API_PASTE_INI = "/etc/glance/glance-api-paste.ini"
CONF_DIR = "/etc/glance"

# Flag used to track config changes.
CONFIG_CHANGED =  False

def execute(cmd, die=False, echo=False):
    """ Executes a command

    if die=True, script will exit(1) if command does not return 0
    if echo=True, output of command will be printed to stdout

    returns a tuple: (stdout, stderr, return code)
    """
    p = subprocess.Popen(cmd.split(" "),
                         stdout=subprocess.PIPE, 
                         stdin=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    stdout = ""
    stderr = ""

    def print_line(l):
        if echo:
            print l.strip('\n')
            sys.stdout.flush()

    for l in iter(p.stdout.readline, ''):
        print_line(l)
        stdout += l
    for l in iter(p.stderr.readline, ''):
        print_line(l)
        stderr += l

    p.communicate()
    rc = p.returncode

    if die and rc != 0:
        error_out("ERROR: command %s return non-zero.\n" % cmd)
    return (stdout, stderr, rc)


function set_or_update {
  local key="$1"
  local value="$2"
  local file="$3"
  local section="$4"
  local conf=""
  [[ -z $key ]] && juju-log "ERROR: set_or_update(): value $value missing key" \
        && exit 1
  [[ -z $value ]] && juju-log "ERROR: set_or_update(): key $key missing value" \
        && exit 1

  case "$file" in
    "api") conf=$GLANCE_API_CONF ;;
    "api-paste") conf=$GLANCE_API_PASTE_INI ;;
    "registry") conf=$GLANCE_REGISTRY_CONF ;;
    "registry-paste") conf=$GLANCE_REGISTRY_PASTE_INI ;;
    *) juju-log "ERROR: set_or_update(): Invalid or no config file specified." \
        && exit 1 ;;
  esac

  [[ ! -e $conf ]] && juju-log "ERROR: set_or_update(): File not found $conf" \
        && exit 1

  if [[ "$(local_config_get "$conf" "$key" "$section")" == "$value" ]] ; then
    juju-log "$CHARM: set_or_update(): $key=$value already set in $conf."
    return 0
  fi

  cfg_set_or_update "$key" "$value" "$conf" "$section"
  CONFIG_CHANGED="True"
}


def do_openstack_upgrade(install_src, packages):
    # update openstack components to those provided by a new installation source
    # it is assumed the calling hook has confirmed that the upgrade is sane.
    config = config_get()
    old_rel = get_os_codename_package('keystone')
    new_rel = get_os_codename_install_source(install_src)

    # Backup previous config.
    utils.juju_log('INFO', "Backing up contents of /etc/glace.")
    stamp = time.strftime('%Y%m%d%H%M')
    cmd = 'tar -pcf /var/lib/juju/keystone-backup-%s.tar /etc/glance' % stamp
    execute(cmd, die=True, echo=True)

    # Setup apt repository access and kick off the actual package upgrade.
    configure_installation_source(install_src)
    execute('apt-get update', die=True, echo=True)
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    cmd = 'apt-get --option Dpkg::Options::=--force-confnew -y '\
          'install %s --no-install-recommends' % packages
    execute(cmd, echo=True, die=True)

    # Update the new config files for existing relations.
    r_id = relation_ids('shared-db')
    if r_id:
        juju_log('INFO', '%s: Configuring database after upgrade to %s.' % (CHARM, install_src))
        db_changed(rid=r_id)

    r_id = relation_ids('identify-server')
    if r_id:
        juju_log('INFO', '%s: Configuring identity service after upgrade to %s' % (CHARM, install_src))
        keystone_changed(rid=r_id)

    ceph_ids = relation_ids('ceph')
    if ceph_ids:
        install('ceph-common', 'python-ceph')
        for r_id in ceph_ids:
            for unit in relation_list(rid):
                ceph_changed(rid=r_id, unit=unit)

    r_id = relation_ids('object-store')
    if r_id:
        object-store_joined()


def configure_https():
    # request openstack-common setup reverse proxy mapping for API and registry
    # servers
    stop('glance-api')

    if len(peer_units()) > 0 or is_clustered():
        # haproxy may already be configured. need to push it back in the request
        # pipeline in preparation for a change from:
        #  from:  haproxy (9292) -> glance_api (9282)
        #  to:    ssl (9292) -> haproxy (9291) -> glance_api (9272)
        next_server = determine_haproxy_port('9292')
        api_port = determine_api_port('9292')
        service_ports = {
            "glance_api": [
                next_server,
                api_port
                ]
            }
        configure_haproxy(service_ports)
    else:
        # if not clustered, the glance-api is next in the pipeline.
        api_port = determine_api_port('9292')
        next_server = api_port

    # TODO:
    # setup_https 9292:$next_server
    # setup https to point to either haproxy or directly to api server, depending.
    setup_https 9292:$next_server

    # TODO:
    # configure servers to listen on new ports accordingly.
    # set_or_update bind_port "$api_port" "api"
    set_or_update()
    start(SERVICES)

    for r_id in relation_ids('identity-service'):
        keystone_joined(relation_id=r_id)
    for r_id in relation_ids('image-service'):
        image-service_joined(relation_id=r_id)
