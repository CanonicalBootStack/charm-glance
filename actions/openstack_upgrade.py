#!/usr/bin/python
import sys

sys.path.append('hooks/')

from charmhelpers.contrib.openstack.utils import (
    openstack_upgrade_available,
    juju_log
)

from glance_utils import (
    do_openstack_upgrade,
    register_configs
)

from glance_relations import (
    config_changed
)

CONFIGS = register_configs()

def openstack_upgrade():
    juju_log('Upgrading OpenStack release')
    do_openstack_upgrade(CONFIGS)
    config_changed()

if __name__ == '__main__':
    openstack_upgrade()
