/* Account entry is same-origin; credentials remain in the account page. */
(()=>{'use strict';
function removeLegacyAccountTab(){document.querySelectorAll('.topbar nav[aria-label="Main navigation"] a').forEach(a=>{if(a.dataset.nav==='family'||a.textContent.trim().toLowerCase()==='account')a.remove();});}
removeLegacyAccountTab();window.addEventListener('pageshow',removeLegacyAccountTab);
const link=document.getElementById('main-sign-in');if(!link)return;let checking=false;
async function refresh(){if(checking)return;checking=true;try{const health=await fetch('/api/health',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)}).then(r=>r.json());link.hidden=false;if(!health.accountsEnabled){link.textContent='Family profiles';link.href='#family';return;}const response=await fetch('/api/accounts/me',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});if(response.ok){link.textContent='My account';link.href='account.html';}else if(response.status===401){link.textContent='Sign in';link.href='account.html?from=main';const badge=document.getElementById('demo-family-pill');if(badge)badge.textContent='Choose a reader';}}catch(_){/* The static account link remains available during connection failure. */}finally{link.setAttribute('aria-label',link.textContent==='My account'?'My parent account: family profiles, story credits and saved books':'Sign in to your parent account');checking=false;}}
refresh();window.addEventListener('pageshow',refresh);window.addEventListener('focus',refresh);
})();
