"""Local document intake and bounded public-HTTPS text fetching; no inference calls."""
import hashlib
from html.parser import HTMLParser
from http.client import HTTPSConnection, HTTPException
from io import BytesIO
import ipaddress
from pathlib import PurePosixPath
import re
import socket
import ssl
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
from zipfile import ZipFile, BadZipFile
from .festival_engine import UserError

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 200000


def clean_text(text):
    return '\n'.join(line for line in (re.sub(r'[\t \r\f\v]+', ' ', line).strip()
                                       for line in text.replace('\x00', '').split('\n')) if line)


def document(content, name):
    if not content or len(content) > MAX_BYTES:
        raise UserError('Choose a document up to 10 MB.', 413)
    name = str(name).replace('\\', '/').rsplit('/', 1)[-1][:180]
    extension = PurePosixPath(name).suffix.lower()
    sections = []; warnings = []
    if extension == '.pdf':
        if not content.startswith(b'%PDF-'): raise UserError('The file is not a valid PDF.', 422)
        try:
            from pypdf import PdfReader
        except ImportError:
            raise UserError('PDF extraction needs pypdf on the local server. TXT and Markdown imports are available.', 503) from None
        try:
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted: raise UserError('Use an unencrypted PDF; password-protected PDFs are not supported.', 422)
            if len(reader.pages) > 100: raise UserError('Use a PDF of at most 100 pages.', 413)
            for index, page in enumerate(reader.pages):
                text = clean_text(page.extract_text() or '')
                sections.append({'location': f'Page {index+1}', 'text': text})
                if not text: warnings.append(f'Page {index+1} has no extractable text; images are not read.')
                if sum(len(s['text']) for s in sections) > MAX_TEXT:
                    raise UserError('The extracted text exceeds 200,000 characters. Split the document first.', 413)
        except UserError: raise
        except Exception:
            raise UserError('This PDF could not be read. Try a text-based, unencrypted PDF or TXT file.', 422) from None
    elif extension == '.docx':
        try:
            with ZipFile(BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
                    raise UserError('The expanded DOCX exceeds 10 MB.', 413)
                xml = archive.read('word/document.xml')
            if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml: raise ValueError()
            root = ElementTree.fromstring(xml)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            for index, paragraph in enumerate(root.findall('.//w:p', ns)):
                text = clean_text(''.join(item.text or '' for item in paragraph.findall('.//w:t', ns)))
                if text: sections.append({'location': f'Paragraph {index+1}', 'text': text})
        except UserError: raise
        except (BadZipFile, KeyError, ValueError, ElementTree.ParseError, RuntimeError):
            raise UserError('This DOCX could not be read. Use a valid .docx or export it as TXT.', 422) from None
    elif extension in ('.txt', '.md', '.markdown'):
        try: text = content.decode('utf-8-sig')
        except UnicodeError: raise UserError('Save TXT or Markdown as UTF-8 before importing.', 422) from None
        sections = [{'location': f'Paragraph {index+1}', 'text': clean_text(text)}
                    for index, text in enumerate(re.split(r'\n\s*\n', text)) if clean_text(text)]
    else:
        raise UserError('Choose PDF, DOCX, Markdown or TXT. Images and legacy .doc are not supported.', 422)
    return extracted(name or 'Source document', extension.lstrip('.'), content, sections, warnings)


def extracted(title, kind, raw, sections, warnings, url=None):
    text = '\n\n'.join(section['text'] for section in sections if section['text'])
    if not text.strip():
        raise UserError('No readable text was found. Scanned PDFs need OCR, which is a future enhancement. Try a text-based PDF or TXT.', 422)
    if len(text) > MAX_TEXT:
        raise UserError('The extracted text exceeds 200,000 characters. Split the source first.', 413)
    return {'title': title[:180], 'kind': kind, 'url': url, 'text': text, 'sections': sections,
            'warnings': warnings, 'wordCount': len(text.split()), 'characterCount': len(text),
            'sourceHash': hashlib.sha256(raw).hexdigest(), 'textHash': hashlib.sha256(text.encode()).hexdigest(),
            'extractionVersion': 'source-intake-v1', 'reviewStatus': 'pending_review',
            'retrievalStatus': 'not_indexed', 'usedForGeneration': False}


def public_target(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii')
        if len(url) > 2000 or any(ord(c) < 33 for c in url) or host.endswith(('.localhost', '.local')):
            raise ValueError()
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError()
        return host, addresses, urlunsplit(('https', parsed.netloc, parsed.path or '/', parsed.query, ''))
    except (ValueError, UnicodeError, OSError):
        raise UserError('Use a public HTTPS link without login credentials. Local/private addresses and nonstandard ports are not supported.', 422) from None


class PinnedHTTPS(HTTPSConnection):
    """Connect to the checked IP while preserving certificate/hostname verification."""
    def __init__(self, host, address):
        context = ssl.create_default_context()
        # Add Mozilla's maintained public roots without disabling OS trust or verification.
        try:
            import certifi
            context.load_verify_locations(cafile=certifi.where())
        except ImportError: pass
        super().__init__(host, timeout=15, context=context); self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        try: self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception: sock.close(); raise


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts = []; self.ignored = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'template', 'head'):
            self.ignored.append(tag)
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'br', 'section') and not self.ignored:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.ignored and tag == self.ignored[-1]: self.ignored.pop()
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'section') and not self.ignored:
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.ignored: self.parts.append(text)


def web_source(url):
    if not isinstance(url, str): raise UserError('Paste a public HTTPS link.', 422)
    for redirect in range(4):
        host, addresses, url = public_target(url.strip())
        parsed = urlsplit(url); connection = PinnedHTTPS(host, addresses[0])
        try:
            path = parsed.path + ('?'+parsed.query if parsed.query else '')
            connection.request('GET', path, headers={'User-Agent': 'BalaKathaSourcePreview/1.0',
                                                     'Accept': 'text/html,text/plain,application/pdf', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = response.getheader('Location')
                if not target or redirect == 3: raise UserError('The link redirects too many times or has no destination.', 422)
                url = urljoin(url, target); continue
            if response.status != 200:
                raise UserError(f'The website returned HTTP {response.status}. Try another public page or upload its document.', 422)
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise UserError('This page requires unsupported compressed delivery. Upload a saved text document instead.', 422)
            content = response.read(MAX_BYTES+1)
            if len(content) > MAX_BYTES: raise UserError('The web source exceeds 10 MB. Use a shorter page.', 413)
            mime = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            charset = response.headers.get_content_charset() or 'utf-8'
        except UserError: raise
        except ssl.SSLCertVerificationError:
            raise UserError('The website certificate could not be verified. Try another public HTTPS page or upload a saved PDF/TXT document.', 422) from None
        except (OSError, ValueError, HTTPException):
            raise UserError('The page could not be fetched. Try a public HTTPS page or upload a saved document.', 422) from None
        finally: connection.close()
        if mime == 'application/pdf':
            source = document(content, host+'.pdf'); source['url'] = url; return source
        if mime not in ('text/html', 'text/plain', 'application/xhtml+xml'):
            raise UserError('Use a public HTML page, plain text page or PDF link.', 422)
        try: text = content.decode(charset, errors='replace')
        except LookupError: text = content.decode('utf-8', errors='replace')
        if mime != 'text/plain':
            parser = VisibleHTML(); parser.feed(text); text = ''.join(parser.parts)
        sections = [{'location': f'Web paragraph {index+1}', 'text': clean_text(text)}
                    for index, text in enumerate(re.split(r'\n\s*\n', text)) if clean_text(text)]
        return extracted(host, 'web', content, sections,
                         ['Text extraction does not run JavaScript, access login-only content or verify cultural accuracy.'], url)
