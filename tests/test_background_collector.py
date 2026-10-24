"""Background collection never takes over ordinary browser tabs."""
import pytest
from dealfinder.collector import BackgroundCollector, valid_job, capture_problem

@pytest.mark.parametrize("job", [
 {"source":"ebay","query":"halo 3","kind":"active","url":"https://www.ebay.com/sch/i.html?_nkw=halo+3"},
 {"source":"ebay","query":"ps2","kind":"sold","url":"https://www.ebay.com/sch/i.html?_nkw=ps2&LH_Sold=1&LH_Complete=1"},
 {"source":"hibid","query":"ps2","kind":"active","url":"https://hibid.com/lots?q=ps2"},
])
def test_only_expected_public_search_jobs(job):
 assert valid_job(job)
 for url in ['http://127.0.0.1:8000/', 'https://evil.example/', job['url'].replace('https://','https://name:secret@'), job['url']+'&LH_Sold=1' if job['source']=='ebay' and job['kind']=='active' else 'https://hibid.com/login']:
  assert not valid_job({**job,'url':url})
 assert not valid_job({**job,'query':'different'})

@pytest.mark.parametrize('title,url', [('Pardon Our Interruption','https://www.ebay.com/sch/i.html'), ('Sign in or Register','https://signin.ebay.com/signin/'), ('Just a moment...','https://hibid.com/lots')])
def test_verification_is_never_captured(title,url):
 assert capture_problem({'title':title,'url':url,'count':0,'text':'','html':''},'ebay')

def test_zero_results_are_success_but_unreadable_pages_need_attention():
 assert not capture_problem({'title':'Search','url':'https://www.ebay.com/sch/i.html','count':0,'text':'0 results','html':'<p>0 results</p>'},'ebay')
 assert capture_problem({'title':'Search','url':'https://www.ebay.com/sch/i.html','count':0,'text':'Loading','html':''},'ebay')

def test_site_failure_isolated_and_commands_are_bounded(tmp_path):
 c=BackgroundCollector('unused', tmp_path/'collector', 'token')
 c.update('ebay',state='needs_attention',message='Sign in required')
 assert c.available_sites() == ['hibid']
 c.command('signin','ebay')
 assert c.next_command() == ('signin','ebay')
 with pytest.raises(ValueError): c.command('signin','unknown')
 with pytest.raises(ValueError): c.command('execute','ebay')
 assert 'token' not in repr(c.snapshot())
 c.command('resume','ebay')
 assert c.next_command() == ('resume','ebay')
 assert c.snapshot()['sites']['ebay']['state']=='needs_attention' # only worker resumes


def test_worker_keeps_other_site_running_and_closes_its_contexts(tmp_path, monkeypatch):
 from types import SimpleNamespace
 import httpx
 import dealfinder.collector as module
 c=BackgroundCollector('http://127.0.0.1:8000',tmp_path/'profiles','local-secret',interval=0)
 launches=[]
 contexts=[]
 class Page:
  def goto(self,url,**kwargs): self.url=url
  def wait_for_function(self,*args,**kwargs): pass
  def evaluate(self,*args):
   if 'ebay' in self.url: return {'url':self.url,'title':'Pardon Our Interruption','count':0,'text':'','html':''}
   return {'url':self.url,'title':'Results','count':0,'text':'No lots found','html':'<div>No lots found</div>'}
 class Context:
  def __init__(self): self.pages=[Page()]; self.closed=False
  def on(self,*args): pass
  def close(self): self.closed=True
 def launch(profile,**kwargs):
  launches.append((profile,kwargs)); context=Context(); contexts.append(context); return context
 original_client=httpx.Client
 jobs=iter([{'source':'ebay','kind':'active','query':'ps2','url':'https://www.ebay.com/sch/i.html?_nkw=ps2'}, {'source':'hibid','kind':'active','query':'ps2','url':'https://hibid.com/lots?q=ps2'}])
 requests=[]
 def handle(request):
  import json
  requests.append((request.url.path,json.loads(request.content)))
  assert request.headers['x-dealfinder-token']=='local-secret'
  if request.url.path=='/browser/next': return httpx.Response(200,json=next(jobs))
  if request.url.path=='/browser/hibid/results': c._stop.set()
  return httpx.Response(200,json={'usable':0,'ok':True})
 monkeypatch.setattr(module.httpx,'Client',lambda **kw:original_client(**kw,transport=httpx.MockTransport(handle)))
 c._collect(SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch)))
 assert [x[1]['sites'] for x in requests if x[0]=='/browser/next']==[['ebay','hibid'],['hibid']]
 assert c.snapshot()['sites']['ebay']['state']=='needs_attention'
 assert c.snapshot()['sites']['hibid']['state']=='ready'
 assert all(kw['headless'] and kw['chromium_sandbox'] and not kw['accept_downloads'] for _,kw in launches)
 assert all(context.closed for context in contexts)
 assert len({profile for profile,_ in launches})==2


def test_signin_window_is_only_opened_on_command_and_resume_returns_headless(tmp_path,monkeypatch):
 from types import SimpleNamespace
 import httpx
 import dealfinder.collector as module
 c=BackgroundCollector('http://127.0.0.1:8000',tmp_path/'profiles','secret',interval=0)
 c.command('signin','ebay'); c.command('resume','ebay')
 launched=[]
 class Page:
  def goto(self,url,**kwargs): pass # Only navigation, never read sign-in fields.
 class Context:
  pages=[Page()]
  def close(self): pass
 def launch(profile,**kw): launched.append(kw); return Context()
 original=httpx.Client
 def handle(request): c._stop.set(); return httpx.Response(200,json={'paused':True})
 monkeypatch.setattr(module.httpx,'Client',lambda **kw:original(**kw,transport=httpx.MockTransport(handle)))
 c._collect(SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch)))
 assert len(launched)==1 and launched[0]['headless'] is False
 assert c.snapshot()['sites']['ebay']['state']=='starting'
