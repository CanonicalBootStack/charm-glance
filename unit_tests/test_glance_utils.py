from mock import patch, call, MagicMock

from collections import OrderedDict
import os

os.environ['JUJU_UNIT_NAME'] = 'glance'
import glance_utils as utils

from test_utils import (
    CharmTestCase,
)

TO_PATCH = [
    'config',
    'log',
    'relation_ids',
    'get_os_codename_package',
    'get_os_codename_install_source',
    'configure_installation_source',
    'eligible_leader',
    'templating',
    'apt_update',
    'apt_upgrade',
    'apt_install',
    'mkdir',
    'service_start',
    'service_stop',
    'service_name',
    'install_alternative'
]

DPKG_OPTS = [
    '--option', 'Dpkg::Options::=--force-confnew',
    '--option', 'Dpkg::Options::=--force-confdef',
]


class TestGlanceUtils(CharmTestCase):

    def setUp(self):
        super(TestGlanceUtils, self).setUp(utils, TO_PATCH)
        self.config.side_effect = self.test_config.get_all

    @patch('subprocess.check_call')
    def test_migrate_database(self, check_call):
        "It migrates database with cinder-manage"
        utils.migrate_database()
        check_call.assert_called_with(['glance-manage', 'db_sync'])

    @patch('os.path.exists')
    def test_register_configs_apache(self, exists):
        exists.return_value = False
        self.get_os_codename_package.return_value = 'grizzly'
        self.relation_ids.return_value = False
        configs = utils.register_configs()
        calls = []
        for conf in [utils.GLANCE_REGISTRY_CONF,
                     utils.GLANCE_API_CONF,
                     utils.GLANCE_API_PASTE_INI,
                     utils.GLANCE_REGISTRY_PASTE_INI,
                     utils.HAPROXY_CONF,
                     utils.HTTPS_APACHE_CONF]:
            calls.append(
                call(conf,
                     utils.CONFIG_FILES[conf]['hook_contexts'])
            )
        configs.register.assert_has_calls(calls, any_order=True)

    @patch('os.path.exists')
    def test_register_configs_apache24(self, exists):
        exists.return_value = True
        self.get_os_codename_package.return_value = 'grizzly'
        self.relation_ids.return_value = False
        configs = utils.register_configs()
        calls = []
        for conf in [utils.GLANCE_REGISTRY_CONF,
                     utils.GLANCE_API_CONF,
                     utils.GLANCE_API_PASTE_INI,
                     utils.GLANCE_REGISTRY_PASTE_INI,
                     utils.HAPROXY_CONF,
                     utils.HTTPS_APACHE_24_CONF]:
            calls.append(
                call(conf,
                     utils.CONFIG_FILES[conf]['hook_contexts'])
            )
        configs.register.assert_has_calls(calls, any_order=True)

    @patch('os.path.exists')
    def test_register_configs_ceph(self, exists):
        exists.return_value = True
        self.get_os_codename_package.return_value = 'grizzly'
        self.relation_ids.return_value = ['ceph:0']
        self.service_name.return_value = 'glance'
        configs = utils.register_configs()
        calls = []
        for conf in [utils.GLANCE_REGISTRY_CONF,
                     utils.GLANCE_API_CONF,
                     utils.GLANCE_API_PASTE_INI,
                     utils.GLANCE_REGISTRY_PASTE_INI,
                     utils.HAPROXY_CONF,
                     utils.ceph_config_file()]:
            calls.append(
                call(conf,
                     utils.CONFIG_FILES[conf]['hook_contexts'])
            )
        configs.register.assert_has_calls(calls, any_order=True)
        self.mkdir.assert_called_with('/etc/ceph')

    def test_restart_map(self):
        self.service_name.return_value = 'glance'

        ex_map = OrderedDict([
            (utils.GLANCE_REGISTRY_CONF, ['glance-registry']),
            (utils.GLANCE_API_CONF, ['glance-api']),
            (utils.GLANCE_API_PASTE_INI, ['glance-api']),
            (utils.GLANCE_REGISTRY_PASTE_INI, ['glance-registry']),
            (utils.ceph_config_file(), ['glance-api', 'glance-registry']),
            (utils.HAPROXY_CONF, ['haproxy']),
            (utils.HTTPS_APACHE_CONF, ['apache2']),
            (utils.HTTPS_APACHE_24_CONF, ['apache2'])
        ])
        self.assertEquals(ex_map, utils.restart_map())

    @patch.object(utils, 'migrate_database')
    def test_openstack_upgrade_leader(self, migrate):
        self.config.side_effect = None
        self.config.return_value = 'cloud:precise-havana'
        self.eligible_leader.return_value = True
        self.get_os_codename_install_source.return_value = 'havana'
        configs = MagicMock()
        utils.do_openstack_upgrade(configs)
        self.assertTrue(configs.write_all.called)
        self.apt_install.assert_called_with(utils.PACKAGES, fatal=True)
        self.apt_upgrade.assert_called_with(options=DPKG_OPTS,
                                            fatal=True, dist=True)
        configs.set_release.assert_called_with(openstack_release='havana')
        self.assertTrue(migrate.called)

    @patch.object(utils, 'migrate_database')
    def test_openstack_upgrade_not_leader(self, migrate):
        self.config.side_effect = None
        self.config.return_value = 'cloud:precise-havana'
        self.eligible_leader.return_value = False
        self.get_os_codename_install_source.return_value = 'havana'
        configs = MagicMock()
        utils.do_openstack_upgrade(configs)
        self.assertTrue(configs.write_all.called)
        self.apt_install.assert_called_with(utils.PACKAGES, fatal=True)
        self.apt_upgrade.assert_called_with(options=DPKG_OPTS,
                                            fatal=True, dist=True)
        configs.set_release.assert_called_with(openstack_release='havana')
        self.assertFalse(migrate.called)
