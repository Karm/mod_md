# test auto runs against ACMEv1

import json
import os
import pytest
import re
import socket
import ssl
import sys
import time

from datetime import datetime
from TestEnv import TestEnv
from TestHttpdConf import HttpdConf
from TestCertUtil import CertUtil


def setup_module(module):
    print("setup_module    module:%s" % module.__name__)
    TestEnv.initv1()
    TestEnv.APACHE_CONF_SRC = "data/test_auto"
    TestEnv.check_acme()
    TestEnv.clear_store()
    HttpdConf().install();
    assert TestEnv.apache_start() == 0
    

def teardown_module(module):
    print("teardown_module module:%s" % module.__name__)
    assert TestEnv.apache_stop() == 0


class TestAutov1:

    def setup_method(self, method):
        print("setup_method: %s" % method.__name__)
        TestEnv.httpd_error_log_clear();
        TestEnv.clear_store()
        self.test_domain = TestEnv.get_method_domain(method)

    def teardown_method(self, method):
        print("teardown_method: %s" % method.__name__)

    # create a MD not used in any virtual host, auto renew should NOT pick it up
    def test_700_001(self):
        # generate config with one MD
        domain = self.test_domain
        domains = [ domain, "www." + domain ]
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_drive_mode( "auto" )
        conf.add_md( domains )
        conf.install()
        #
        # restart, check that MD is synched to store
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md(domains)
        stat = TestEnv.get_md_status(domain)
        assert stat["watched"] == 0
        #
        # add vhost for MD, restart should drive it
        conf.add_vhost(domains)
        conf.install()
        assert TestEnv.apache_restart() == 0
        assert TestEnv.await_completion([ domain ] )
        TestEnv.check_md_complete(domain)
        stat = TestEnv.get_md_status(domain)
        assert stat["watched"] == 1
        #
        cert = TestEnv.get_cert(domain)
        assert domain in cert.get_san_list()
        #
        # challenges should have been removed
        # file system needs to have correct permissions
        TestEnv.check_dir_empty( TestEnv.store_challenges() )
        TestEnv.check_file_permissions( domain )

    # test case: same as test_7001, but with two parallel managed domains
    def test_700_002(self):
        # generate config with two MDs
        domain = self.test_domain
        domainA = "a-" + domain
        domainB = "b-" + domain
        domainsA = [ domainA, "www." + domainA ]
        domainsB = [ domainB, "www." + domainB ]
        conf = HttpdConf()
        conf.add_admin( "admin@not-forbidden.org" )
        conf.add_drive_mode( "auto" )
        conf.add_md(domainsA)
        conf.add_md(domainsB)
        conf.add_vhost(domainsA)
        conf.add_vhost(domainsB)
        conf.install()
        #
        # restart, check that md is in store
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md( domainsA )
        TestEnv.check_md( domainsB )
        # await drive completion
        assert TestEnv.await_completion( [ domainA, domainB ] )
        TestEnv.check_md_complete(domainA)
        TestEnv.check_md_complete(domainB)
        #
        # check: SSL is running OK
        certA = TestEnv.get_cert(domainA)
        assert domainsA == certA.get_san_list()
        certB = TestEnv.get_cert(domainB)
        assert domainsB == certB.get_san_list()
        #
        # should have a single account now
        assert 1 == len(TestEnv.list_accounts())

    # test case: one MD, that covers two vhosts
    def test_700_003(self):
        # generate 1 MD and 2 vhosts
        domain = self.test_domain
        nameA = "a." + domain
        nameB = "b." + domain
        domains = [ domain, nameA, nameB ]
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_md( domains )
        conf.add_vhost(nameA, docRoot="htdocs/a")
        conf.add_vhost(nameB, docRoot="htdocs/b")
        conf.install()
        #
        # create docRoot folder
        self._write_res_file( os.path.join(TestEnv.APACHE_HTDOCS_DIR, "a"), "name.txt", nameA )
        self._write_res_file( os.path.join(TestEnv.APACHE_HTDOCS_DIR, "b"), "name.txt", nameB )
        #
        # restart (-> drive), check that MD was synched and completes
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md( domains )
        assert TestEnv.await_completion( [ domain, nameA, nameB ] )
        TestEnv.check_md_complete(domain)
        #
        # check: SSL is running OK
        certA = TestEnv.get_cert(nameA)
        assert nameA in certA.get_san_list()
        certB = TestEnv.get_cert(nameB)
        assert nameB in certB.get_san_list()
        assert certA.get_serial() == certB.get_serial()
        #      
        assert TestEnv.get_content( nameA, "/name.txt" ) == nameA
        assert TestEnv.get_content( nameB, "/name.txt" ) == nameB


    # test case: drive with using single challenge type explicitly
    @pytest.mark.parametrize("challengeType", [
        ("tls-alpn-01"), 
        ("http-01")
    ])
    def test_700_004(self, challengeType):
        # generate 1 MD and 1 vhost
        domain = self.test_domain
        domains = [ domain, "www." + domain ]
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_line( "Protocols http/1.1 acme-tls/1" )
        conf.add_drive_mode( "auto" )
        conf.add_ca_challenges( [ challengeType ] )
        conf.add_md( domains )
        conf.add_vhost(domains)
        conf.install()
        #
        # restart (-> drive), check that MD was synched and completes
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md(domains)
        assert TestEnv.await_completion( [ domain ] )
        TestEnv.check_md_complete(domain)
        #        
        # check SSL running OK
        cert = TestEnv.get_cert(domain)
        assert domain in cert.get_san_list()

    # test case: drive_mode manual, check that server starts, but requests to domain are 503'd
    def test_700_005(self):
        # generate 1 MD and 1 vhost
        domain = self.test_domain
        nameA = "a." + domain
        domains = [ domain, nameA ]
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_drive_mode( "manual" )
        conf.add_md( domains )
        conf.add_vhost(nameA, docRoot="htdocs/a")
        conf.install()
        #
        # create docRoot folder
        self._write_res_file(os.path.join(TestEnv.APACHE_HTDOCS_DIR, "a"), "name.txt", nameA)
        #
        # restart, check that md is in store
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md(domains)
        #        
        # check: that request to domains give 503 Service Unavailable
        cert1 = TestEnv.get_cert(nameA)
        assert nameA in cert1.get_san_list()
        assert TestEnv.getStatus(nameA, "/name.txt") == 503
        #
        # check temporary cert from server
        cert2 = CertUtil( TestEnv.path_fallback_cert( domain ) )
        assert cert1.get_serial() == cert2.get_serial(), \
            "Unexpected temporary certificate on vhost %s. Expected cn: %s , but found cn: %s" % ( nameA, cert2.get_cn(), cert1.get_cn() )

    # test case: drive MD with only invalid challenges, domains should stay 503'd
    def test_700_006(self):
        # generate 1 MD, 1 vhost
        domain = self.test_domain
        nameA = "a." + domain
        domains = [ domain, nameA ]
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_ca_challenges([ "invalid-01", "invalid-02" ])
        conf.add_md( domains )
        conf.add_vhost(nameA, docRoot="htdocs/a")
        conf.install()
        #
        # create docRoot folder
        self._write_res_file(os.path.join(TestEnv.APACHE_HTDOCS_DIR, "a"), "name.txt", nameA)
        #
        # restart, check that md is in store
        assert TestEnv.apache_restart() == 0
        # await drive completion
        md = TestEnv.await_error(domain)
        assert md
        assert md['renewal']['errors'] > 0
        assert md['renewal']['last']['problem'] == 'challenge-mismatch'
        assert 'account' not in md['ca']
        #
        # check: that request to domains give 503 Service Unavailable
        cert = TestEnv.get_cert(nameA)
        assert nameA in cert.get_san_list()
        assert TestEnv.getStatus(nameA, "/name.txt") == 503

    # Specify a non-working http proxy
    def test_700_008(self):
        domain = self.test_domain
        domains = [ domain ]
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_drive_mode( "always" )
        conf.add_http_proxy( "http://localhost:1" )
        conf.add_md( domains )
        conf.install()
        #
        # - restart (-> drive)
        assert TestEnv.apache_restart() == 0
        # await drive completion
        md = TestEnv.await_error(domain)
        assert md
        assert md['renewal']['errors'] > 0
        assert md['renewal']['last']['status-description'] == 'Connection refused'
        assert 'account' not in md['ca']

    # Specify a valid http proxy
    def test_700_008a(self):
        domain = self.test_domain
        domains = [ domain ]
        conf = HttpdConf(proxy=True)
        conf.add_admin( "admin@" + domain )
        conf.add_drive_mode( "always" )
        conf.add_http_proxy( "http://localhost:%s"  % TestEnv.HTTP_PROXY_PORT)
        conf.add_md( domains )
        conf.install()
        #
        # - restart (-> drive), check that md is in store
        assert TestEnv.apache_restart() == 0
        assert TestEnv.await_completion( [ domain ] )
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md_complete(domain)

    # Force cert renewal due to critical remaining valid duration
    # Assert that new cert activation is delayed
    def test_700_009(self):
        domain = self.test_domain
        domains = [ domain ]
        # prepare md
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_drive_mode( "auto" )
        conf.add_renew_window( "10d" )
        conf.add_md( domains )
        conf.add_vhost(domain)
        conf.install()
        #
        # restart (-> drive), check that md+cert is in store, TLS is up
        assert TestEnv.apache_restart() == 0
        assert TestEnv.await_completion( [ domain ] )
        TestEnv.check_md_complete(domain)
        cert1 = CertUtil( TestEnv.store_domain_file(domain, 'pubcert.pem') )
        # compare with what md reports as status
        stat = TestEnv.get_certificate_status(domain);
        assert stat['serial'] == cert1.get_serial()
        #
        # create self-signed cert, with critical remaining valid duration -> drive again
        TestEnv.create_self_signed_cert( [domain], { "notBefore": -120, "notAfter": 2  }, serial=7009)
        cert3 = CertUtil( TestEnv.store_domain_file(domain, 'pubcert.pem') )
        assert cert3.get_serial() == '1B61'
        assert TestEnv.apache_restart() == 0
        stat = TestEnv.get_certificate_status(domain);
        assert stat['serial'] == cert3.get_serial()
        #
        # cert should renew and be different afterwards
        assert TestEnv.await_completion( [ domain ], must_renew=True )
        stat = TestEnv.get_certificate_status(domain);
        assert stat['serial'] != cert3.get_serial()
        
    # test case: drive with an unsupported challenge due to port availability 
    def test_700_010(self):
        domain = self.test_domain
        domains = [ domain, "www." + domain ]
        # generate 1 MD and 1 vhost, map port 80 onto itself where the server does not listen
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_drive_mode( "auto" )
        conf.add_ca_challenges( [ "http-01" ] )
        conf._add_line("MDPortMap http:99")        
        conf.add_md( domains )
        conf.add_vhost(domains)
        conf.install()
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md(domains)
        assert not TestEnv.is_renewing( domain )
        #
        # now the same with a 80 mapped to a supported port 
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_drive_mode( "auto" )
        conf.add_ca_challenges( [ "http-01" ] )
        conf._add_line("MDPortMap http:%s" % TestEnv.HTTP_PORT)
        conf.add_md( domains )
        conf.add_vhost(domains)
        conf.install()
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md(domains)
        assert TestEnv.await_completion( [ domain ] )

    def test_700_011(self):
        domain = self.test_domain
        domains = [ domain, "www." + domain ]
        # generate 1 MD and 1 vhost, map port 443 onto itself where the server does not listen
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_line( "Protocols http/1.1 acme-tls/1" )
        conf.add_drive_mode( "auto" )
        conf.add_ca_challenges( [ "tls-alpn-01" ] )
        conf._add_line("MDPortMap 443:99")        
        conf.add_md( domains )
        conf.add_vhost(domains)
        conf.install()
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md(domains)
        assert not TestEnv.is_renewing( domain )
        #
        # now the same with a 443 mapped to a supported port 
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_line( "Protocols http/1.1 acme-tls/1" )
        conf.add_drive_mode( "auto" )
        conf.add_ca_challenges( [ "tls-alpn-01" ] )
        conf._add_line("MDPortMap 443:%s" % TestEnv.HTTPS_PORT)
        conf.add_md( domains )
        conf.add_vhost(domains)
        conf.install()
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md(domains)
        assert TestEnv.await_completion( [ domain ] )

    # test case: one MD with several dns names. sign up. remove the *first* name
    # in the MD. restart. should find and keep the existing MD.
    # See: https://github.com/icing/mod_md/issues/68
    def test_700_030(self):
        domain = self.test_domain
        nameX = "x." + domain
        nameA = "a." + domain
        nameB = "b." + domain
        domains = [ nameX, nameA, nameB ]
        #
        # generate 1 MD and 2 vhosts
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_md( domains )
        conf.add_vhost(nameA)
        conf.add_vhost(nameB)
        conf.install()
        #
        # restart (-> drive), check that MD was synched and completes
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md( domains )
        assert TestEnv.await_completion( [ nameX ] )
        TestEnv.check_md_complete(nameX)
        #
        # check: SSL is running OK
        certA = TestEnv.get_cert(nameA)
        assert nameA in certA.get_san_list()
        certB = TestEnv.get_cert(nameB)
        assert nameB in certB.get_san_list()
        assert certA.get_serial() == certB.get_serial()
        #        
        # change MD by removing 1st name
        new_list = [ nameA, nameB ]
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_md( new_list )
        conf.add_vhost(nameA)
        conf.add_vhost(nameB)
        conf.install()
        # restart, check that host still works and have same cert
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md( new_list )
        status = TestEnv.get_certificate_status( nameA )
        assert status['serial'] == certA.get_serial() 

    # test case: Same as 7030, but remove *and* add another at the same time.
    # restart. should find and keep the existing MD and renew for additional name.
    # See: https://github.com/icing/mod_md/issues/68
    def test_700_031(self):
        domain = self.test_domain
        nameX = "x." + domain
        nameA = "a." + domain
        nameB = "b." + domain
        nameC = "c." + domain
        domains = [ nameX, nameA, nameB ]
        #
        # generate 1 MD and 2 vhosts
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_md( domains )
        conf.add_vhost(nameA)
        conf.add_vhost(nameB)
        conf.install()
        #
        # restart (-> drive), check that MD was synched and completes
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md( domains )
        assert TestEnv.await_completion( [ nameX ] )
        TestEnv.check_md_complete(nameX)
        #
        # check: SSL is running OK
        certA = TestEnv.get_cert(nameA)
        assert nameA in certA.get_san_list()
        certB = TestEnv.get_cert(nameB)
        assert nameB in certB.get_san_list()
        assert certA.get_serial() == certB.get_serial()
        #        
        # change MD by removing 1st name
        new_list = [ nameA, nameB, nameC ]
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf.add_md( new_list )
        conf.add_vhost(nameA)
        conf.add_vhost(nameB)
        conf.install()
        # restart, check that host still works and have same cert
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md( new_list )
        status = TestEnv.get_certificate_status( nameA )
        assert status['serial'] == certA.get_serial() 

    # test case: create two MDs, move them into one
    # see: <https://bz.apache.org/bugzilla/show_bug.cgi?id=62572>
    def test_700_032(self):
        domain = self.test_domain
        name1 = "server1." + domain
        name2 = "server2.b" + domain # need a separate TLD to avoid rate limites
        #
        # generate 2 MDs and 2 vhosts
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf._add_line( "MDMembers auto" )
        conf.add_md([ name1 ])
        conf.add_md([ name2 ])
        conf.add_vhost(name1)
        conf.add_vhost(name2)
        conf.install()
        #
        # restart (-> drive), check that MD was synched and completes
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md([ name1 ])
        TestEnv.check_md([ name2 ])
        assert TestEnv.await_completion( [ name1, name2 ] )
        TestEnv.check_md_complete(name2)
        #
        # check: SSL is running OK
        cert1 = TestEnv.get_cert(name1)
        assert name1 in cert1.get_san_list()
        cert2 = TestEnv.get_cert(name2)
        assert name2 in cert2.get_san_list()
        #        
        # remove second md and vhost, add name2 to vhost1
        conf = HttpdConf()
        conf.add_admin( "admin@" + domain )
        conf._add_line( "MDMembers auto" )
        conf.add_md( [ name1 ] )
        conf.add_vhost([ name1, name2 ], docRoot="htdocs/a")
        conf.install()
        assert TestEnv.apache_restart() == 0
        TestEnv.check_md([ name1, name2 ])
        assert TestEnv.await_completion([ name1 ])
        #
        cert1b = TestEnv.get_cert(name1)
        assert name1 in cert1b.get_san_list()
        assert name2 in cert1b.get_san_list()
        assert cert1.get_serial() != cert1b.get_serial()


    # --------- _utils_ ---------
    def _write_res_file(self, docRoot, name, content):
        if not os.path.exists(docRoot):
            os.makedirs(docRoot)
        open(os.path.join(docRoot, name), "w").write(content)

