'use strict';
function renderIndependentReviewer(){
 const button=$('run-independent-reviewer'),output=$('independent-reviewer-output');
 if(!festivalRecord)return;
 const exhausted=festivalRecord.correctionCyclesUsed>=festivalRecord.maxCorrectionCycles&&!festivalRecord.qaReady;
 button.disabled=festivalDirty||!festivalHealth?.reviewerConfigured||exhausted;
 const ready=Boolean(festivalRecord.qaReady)&&!festivalDirty;
 document.querySelector('.festival-approval').hidden=!ready;
 $('festival-stage-heading').textContent=ready?'PARENT STORY REVIEW':'DRAFT & QA WORKSPACE';
 $('festival-approve').disabled=!ready||festivalRecord.approved;
 $('festival-pdf').disabled=!ready||!festivalRecord.approved;
 $('festival-narrate').disabled=!ready||!festivalRecord.approved||!festivalHealth?.narrationConfigured;
 const routes=festivalRecord.reviewer?.report?.routes||[];
 $('run-qa-correction').disabled=festivalDirty||exhausted||ready||!festivalHealth?.reviewerConfigured||!routes.length||routes.some(route=>!['Story Generation Agent','Child Safety & Cultural Review Agent'].includes(route));
 if(!ready)$('festival-result-status').textContent='Draft workspace only. Parent approval, PDF and narration stay locked until full QA passes for this exact version.';
 const saved=festivalRecord.reviewer;
 output.replaceChildren();
 if(!saved){
  output.append(node('p','small muted',festivalHealth?.reviewerConfigured?'QA has not completed. Run full QA before parent review.':'QA is not connected. A Nebius connection is needed; no PASS has been generated. Parent approval and exports remain locked.'));
  return;
 }
 const report=saved.report||{},current=saved.configurationCurrent!==false;
 output.append(node('p','small muted',`Correction cycles: ${festivalRecord.correctionCyclesUsed||0} / ${festivalRecord.maxCorrectionCycles||2}.`));
 if(Array.isArray(report.routes)&&report.routes.length)output.append(node('p','small',`Send corrections to: ${report.routes.join(', ')}.`));
 if(exhausted)output.append(node('p','small error','Two correction cycles have been used. Stop and resolve the remaining notes with the parent; this story is not ready.'));
 const badge=node('span','reviewer-verdict '+(current&&report.verdict==='PASS'?'reviewer-pass':'reviewer-revise'),current?report.verdict||'REVISION_REQUIRED':'OUTDATED REVIEW');
 output.append(badge,node('p','small',report.summary||'Review needs revision; inspect the findings below.'),node('p','small muted',`Saved version ${saved.bookVersion} · Playbook ${saved.playbookVersion}. This report covers the evidence snapshot captured when it ran.`));
 if(!current)output.append(node('p','small error','The review prompt or Playbook changed. Run a fresh review before treating this as current.'));
 const evidence=(host,refs)=>{
  if(!Array.isArray(refs))return;
  refs.forEach(ref=>{if(!ref||typeof ref!=='object')return;const line=node('p','reviewer-citation');line.append(node('strong','',`${ref.artifact_id||'?'}${ref.pointer||''}: `),document.createTextNode(String(ref.quote||'')));host.append(line);});
 };
 const dimensions=node('div','reviewer-dimensions');
 if(Array.isArray(report.dimensions))report.dimensions.forEach(row=>{if(!row||typeof row!=='object')return;const item=node('details','reviewer-finding'),summary=node('summary','',`${row.name}: ${row.status}`);item.append(summary,node('p','small',row.note||''));evidence(item,row.evidence);dimensions.append(item);});
 output.append(dimensions);
 if(Array.isArray(report.revisions))report.revisions.forEach(revision=>{if(!revision||typeof revision!=='object')return;const item=node('div','reviewer-revision');item.append(node('strong','',`${revision.rule_id||'Review'} ${revision.artifact_pointer||''}`),node('p','small',revision.issue||''),node('p','small',`Requested change: ${revision.requested_change||'Supply the missing evidence or revise this artifact.'}`));output.append(item);});
 if(Array.isArray(report.validationNotes)&&report.validationNotes.length){const list=node('ul','small error');report.validationNotes.forEach(note=>list.append(node('li','',String(note))));output.append(list);}
 const details=node('details','reviewer-finding');details.append(node('summary','','Claim-by-claim grounding and Playbook checks'));
 if(Array.isArray(report.claims))report.claims.forEach(claim=>{if(!claim||typeof claim!=='object')return;const item=node('div','reviewer-claim');item.append(node('strong','',`${claim.artifact_pointer||''} · ${claim.decision||'Unresolved'}`),node('p','small',claim.claim||''),node('p','small',claim.note||''));evidence(item,claim.evidence);details.append(item);});
 if(Array.isArray(report.rules))report.rules.forEach(rule=>{if(!rule||typeof rule!=='object')return;const item=node('div','reviewer-claim');item.append(node('strong','',`${rule.rule_id}: ${rule.status}`),node('p','small',rule.note||''));evidence(item,rule.evidence);details.append(item);});
 output.append(details);
 const audit=node('a','festival-source','Open saved review and its evidence snapshot ↗');audit.href=`/api/books/${festivalRecord.id}/review-evidence`;audit.target='_blank';audit.rel='noopener';output.append(audit);
}
function setupIndependentReviewer(){
 $('run-independent-reviewer').addEventListener('click',async()=>{
  if(!festivalRecord||festivalDirty)return;
  const target={id:festivalRecord.id,version:festivalRecord.version},button=$('run-independent-reviewer');
  button.disabled=true;$('independent-reviewer-output').replaceChildren(node('p','small','Checking this saved version against the Playbook and supplied evidence…'));
  try{
   const result=await festivalApi(`/books/${target.id}/review`,{method:'POST',body:JSON.stringify({version:target.version})});
   if(festivalDirty||festivalRecord.id!==target.id||festivalRecord.version!==target.version){$('independent-reviewer-output').replaceChildren(node('p','small','The review was saved for the earlier version. Save your edits and review the current version.'));return;}
   festivalRecord=result;renderIndependentReviewer();
  }catch(error){$('independent-reviewer-output').replaceChildren(node('p','small error',error.message));}
  finally{button.disabled=festivalDirty||!festivalHealth?.reviewerConfigured||(festivalRecord.correctionCyclesUsed>=festivalRecord.maxCorrectionCycles&&!festivalRecord.qaReady);}
 });
 $('run-qa-correction').addEventListener('click',async()=>{
  if(!festivalRecord||festivalDirty)return;
  const target={id:festivalRecord.id,version:festivalRecord.version};
  $('run-qa-correction').disabled=true;$('run-independent-reviewer').disabled=true;
  $('independent-reviewer-output').replaceChildren(node('p','small','Sending notes to the responsible agents, then checking the complete revised draft. This uses AI calls.'));
  try{
   const result=await festivalApi(`/books/${target.id}/correct`,{method:'POST',body:JSON.stringify({version:target.version,confirmPaid:true})});
   if(festivalRecord.id!==target.id||festivalRecord.version!==target.version||festivalDirty){$('festival-result-status').textContent='Correction was saved separately. Reload this saved book before continuing.';return;}
   festivalRecord=result;renderFestivalBook();
  }catch(error){renderIndependentReviewer();$('festival-result-status').textContent=error.message;}
 });
}
