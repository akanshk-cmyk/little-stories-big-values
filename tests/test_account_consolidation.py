"""Scope protection for the in-app account consolidation."""
import hashlib,json
from html.parser import HTMLParser
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
BASE=json.loads((ROOT/'docs/verification/account-consolidation-baseline.json').read_text())
class ScreenParser(HTMLParser):
    def __init__(self,text):
        super().__init__(convert_charrefs=False);self.text=text;self.depth=0;self.current=None;self.screens={};self.ids=[]
    def position(self):
        line,col=self.getpos();return sum(len(x) for x in self.text.splitlines(keepends=True)[:line-1])+col
    def handle_starttag(self,tag,attrs):
        ident=dict(attrs).get('id')
        if ident:self.ids.append(ident)
        if tag=='section':
            self.depth+=1
            if self.current is None and ident in BASE['screens']:self.current=(ident,self.position(),self.depth)
    def handle_endtag(self,tag):
        if tag=='section':
            if self.current and self.current[2]==self.depth:
                name,start,_=self.current;self.screens[name]=self.text[start:self.position()+10];self.current=None
            self.depth-=1
class ScopeTests(unittest.TestCase):
    def test_story_screens_remain_present_after_authorized_changes(self):
        parser=ScreenParser((ROOT/'prototype-v2/index.html').read_text(encoding='utf-8'));parser.feed(parser.text)
        self.assertEqual(set(parser.screens),set(BASE['screens']))
        for name,source in parser.screens.items():
            with self.subTest(screen=name):self.assertIn('id="'+name+'"',source)
    def test_existing_controllers_remain_and_generation_uses_account_workflow(self):
        for name in BASE['files']:
            with self.subTest(file=name):self.assertTrue((ROOT/'prototype-v2'/name).is_file())
        managed=(ROOT/'prototype-v2/managed-story-ui.js').read_text(encoding='utf-8')
        self.assertIn("api('runs'",managed)
        self.assertIn("api('preflight',request)",managed)
        self.assertIn('stopImmediatePropagation',managed)
    def test_single_account_and_existing_checkout_anchors(self):
        source=(ROOT/'prototype-v2/index.html').read_text(encoding='utf-8');parser=ScreenParser(source);parser.feed(source)
        self.assertEqual(len(parser.ids),len(set(parser.ids)))
        self.assertEqual(source.count('data-nav="family"'),0)
        self.assertEqual(source.count('id="main-sign-in"'),1)
        self.assertIn('<span id="demo-family-pill"',source)
        for ident in ['demo-checkout-content','demo-success-content','demo-failure-content','demo-child-form','demo-parent-form','demo-billing-history']:
            self.assertIn(ident,parser.ids)
if __name__=='__main__':unittest.main()
