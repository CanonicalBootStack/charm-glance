import glance_contexts as contexts
from mock import patch

from test_utils import (
    CharmTestCase
)

TO_PATCH = [
    'relation_ids',
    'is_relation_made',
    'service_name',
    'determine_apache_port',
    'determine_api_port',
    'get_ipv6_addr',
]


class TestGlanceContexts(CharmTestCase):

    def setUp(self):
        super(TestGlanceContexts, self).setUp(contexts, TO_PATCH)

    def test_swift_not_related(self):
        self.relation_ids.return_value = []
        self.assertEquals(contexts.ObjectStoreContext()(), {})

    def test_swift_related(self):
        self.relation_ids.return_value = ['object-store:0']
        self.assertEquals(contexts.ObjectStoreContext()(),
                          {'swift_store': True})

    def test_ceph_not_related(self):
        self.is_relation_made.return_value = False
        self.assertEquals(contexts.CephGlanceContext()(), {})

    def test_ceph_related(self):
        self.is_relation_made.return_value = True
        service = 'glance'
        self.service_name.return_value = service
        self.assertEquals(
            contexts.CephGlanceContext()(),
            {'rbd_pool': service,
             'rbd_user': service})

    @patch('charmhelpers.contrib.openstack.context.is_clustered')
    @patch('charmhelpers.contrib.openstack.context.determine_apache_port')
    @patch('charmhelpers.contrib.openstack.context.determine_api_port')
    @patch('charmhelpers.contrib.openstack.context.unit_get')
    @patch('charmhelpers.contrib.openstack.context.https')
    def test_apache_ssl_context_service_enabled(self, mock_https,
                                                mock_unit_get,
                                                mock_determine_api_port,
                                                mock_determine_apache_port,
                                                mock_is_clustered):
        mock_https.return_value = True
        mock_unit_get.return_value = '1.2.3.4'
        mock_determine_api_port.return_value = '12'
        mock_determine_apache_port.return_value = '34'
        mock_is_clustered.return_value = False

        ctxt = contexts.ApacheSSLContext()
        with patch.object(ctxt, 'enable_modules') as mock_enable_modules:
            with patch.object(ctxt, 'configure_cert') as mock_configure_cert:
                self.assertEquals(ctxt(), {'endpoints': [(34, 12)],
                                           'private_address': '1.2.3.4',
                                           'namespace': 'glance'})
                self.assertTrue(mock_https.called)
                mock_unit_get.assert_called_with('private-address')
                self.assertTrue(mock_enable_modules.called)
                self.assertTrue(mock_configure_cert.called)

    @patch('glance_contexts.config')
    def test_glance_ipv6_context_service_enabled(self, mock_config):
        mock_config.return_value = True
        self.get_ipv6_addr.return_value = ['2001:db8:1::1']
        ctxt = contexts.GlanceIPv6Context()
        self.assertEquals(ctxt(), {'bind_host': '2001:db8:1::1',
                                   'registry_host': '[2001:db8:1::1]'})

    @patch('glance_contexts.config')
    def test_glance_ipv6_context_service_disabled(self, mock_config):
        mock_config.return_value = False
        ctxt = contexts.GlanceIPv6Context()
        self.assertEquals(ctxt(), {'bind_host': '0.0.0.0',
                                   'registry_host': '0.0.0.0'})
