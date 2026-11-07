"""Source intake contracts with fixture documents and mocked web responses; no evals."""
from email.message import Message
from io import BytesIO
import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from zipfile import ZipFile
import test_festivals as api_tests
from backend import source_ingestion as intake
from backend.festival_engine import UserError


def fixture_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 250 Td (Mira shares paper flowers with a friend.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    output=BytesIO();writer.write(output);return output.getvalue()


class SourceExtraction(unittest.TestCase):
    def test_pdf_extracts_actual_text_and_page_location(self):
        result=intake.document(fixture_pdf(),'kindness.pdf')
        self.assertIn('Mira shares',result['text']);self.assertEqual(result['sections'][0]['location'],'Page 1')
        self.assertEqual(result['reviewStatus'],'pending_review');self.assertEqual(result['retrievalStatus'],'not_indexed')
        self.assertFalse(result['usedForGeneration'])
    def test_empty_scanned_and_invalid_pdf_are_explicit_errors(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);output=BytesIO();writer.write(output)
        for content in (output.getvalue(),b'not a pdf'):
            with self.assertRaises(UserError):intake.document(content,'scanned.pdf')
    def test_docx_and_utf8_markdown(self):
        output=BytesIO()
        with ZipFile(output,'w') as archive:
            archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Mira is kind.</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(intake.document(output.getvalue(),'story.docx')['text'],'Mira is kind.')
        self.assertIn('Sharing',intake.document(b'# Sharing\n\nMira shares a flower.','notes.md')['text'])
    def test_private_links_ports_and_schemes_are_rejected(self):
        with patch('backend.source_ingestion.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
            for url in ('https://localhost/','https://example.org/','file:///secret','http://example.org/','https://user:pass@example.org/','https://example.org:8443/'):
                with self.assertRaises(UserError):intake.public_target(url)
    def test_web_text_fetch_does_not_execute_script_or_mark_reviewed(self):
        class Response:
            status=200;headers=Message();headers['Content-Type']='text/html; charset=utf-8'
            def getheader(self,key,default=None):return self.headers.get(key,default)
            def read(self,limit):return b'<html><head><title>Ignored head</title></head><body><h1>Kindness</h1><p>Mira shares a flower.</p><script>stealSecrets()</script></body></html>'
        with patch('backend.source_ingestion.public_target',return_value=('example.org',['93.184.216.34'],'https://example.org/')),patch('backend.source_ingestion.PinnedHTTPS') as connection:
            connection.return_value.getresponse.return_value=Response()
            result=intake.web_source('https://example.org/')
        self.assertIn('Mira shares',result['text']);self.assertNotIn('stealSecrets',result['text'])
        self.assertEqual(result['reviewStatus'],'pending_review');self.assertEqual(result['url'],'https://example.org/')
        connection.assert_called_once_with('example.org','93.184.216.34')
    def test_redirect_destination_is_revalidated(self):
        class Response:
            status=302
            def getheader(self,key,default=None):return 'https://127.0.0.1/secret'
        with patch('backend.source_ingestion.public_target',side_effect=[('example.org',['93.184.216.34'],'https://example.org/'),UserError('Private address rejected.',422)]) as target,patch('backend.source_ingestion.PinnedHTTPS') as connection:
            connection.return_value.getresponse.return_value=Response()
            with self.assertRaises(UserError):intake.web_source('https://example.org/')
        self.assertEqual(target.call_count,2);self.assertEqual(connection.call_count,1)


class SourceApi(unittest.TestCase):
    setUpClass=api_tests.ApprovalApi.__dict__['setUpClass']
    tearDownClass=api_tests.ApprovalApi.__dict__['tearDownClass']
    setUp=api_tests.ApprovalApi.setUp
    call=api_tests.ApprovalApi.call

    def upload(self,content=b'Mira shares flowers.',name='notes.txt',token=True):
        headers={'Content-Type':'application/octet-stream','X-Source-Name':name}
        if token:headers['X-Bala-CSRF']=self.csrf
        request=Request(self.base+'/api/sources/upload',data=content,headers=headers,method='POST')
        try:response=self.client.open(request)
        except HTTPError as error:response=error
        with response:return response.status,json.load(response)

    def test_upload_list_preview_and_ownership(self):
        status,source=self.upload(fixture_pdf(),'source.pdf');self.assertEqual(status,201)
        self.assertIn('Mira shares',source['text']);self.assertEqual(source['reviewStatus'],'pending_review')
        status,sources=self.call('/api/sources');self.assertEqual(status,200)
        self.assertTrue(any(row['id']==source['id'] for row in sources));self.assertNotIn('text',sources[0])
        status,preview=self.call('/api/sources/'+source['id']);self.assertEqual(status,200);self.assertEqual(preview['textHash'],source['textHash'])
        status,_=self.call('/api/sources/'+source['id'],client=build_opener());self.assertEqual(status,404)
    def test_upload_needs_csrf_and_supported_type(self):
        status,_=self.upload(token=False);self.assertEqual(status,403)
        status,_=self.upload(name='unsafe.exe');self.assertEqual(status,422)
    def test_url_import_has_separate_pending_source_status(self):
        fixture=intake.document(b'Mira shares a flower.','reference.txt');fixture['url']='https://example.org/story';fixture['kind']='web'
        with patch('backend.server.web_source',return_value=fixture):
            status,source=self.call('/api/sources/url',{'url':'https://example.org/story'})
        self.assertEqual(status,201);self.assertFalse(source['usedForGeneration']);self.assertEqual(source['retrievalStatus'],'not_indexed')
        self.assertEqual(source['reviewStatus'],'pending_review')
