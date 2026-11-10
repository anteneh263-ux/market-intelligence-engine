'use strict';
// Local intent matching; no model, network requests, or persistent conversation.
function answerReportQuestion(d, question) {
  const q=question.toLocaleLowerCase('nb-NO').trim();
  const answer=(text,source)=>({text,source});
  const source=d.source+' · lagret rapport og nøkkeltall';
  const fallback=()=>answer('Jeg kan ikke svare sikkert på dette med den lokale spørsmålshjelpen. Prøv et av forslagene, eller se originalrapporten. Jeg tolker ikke fri samtale og husker ikke tidligere spørsmål.','Ingen kilde som besvarer spørsmålet.');
  if(!q||q.length>500)return fallback();
  if(/\b(kjøpe|selge|investere|morgen|fremtid|framtid|garanti)\b/.test(q))return answer('Denne rapporten gir ikke grunnlag for å si hva du bør kjøpe eller selge, eller hva du vil tjene fremover. Jeg kan forklare den historiske undersøkelsen.',source);
  if(/neste steg|undersøke videre|neste handling/.test(q)){
    const r=recommendNextStep(d);return answer(`${r.recommendation}.\n\nHvorfor: ${r.why}\n\nNeste handling: ${r.action}\n\nHva mangler? ${r.missing}`,source+' · regelbasert veiledning');
  }
  if(/hva (betyr|er)|forklar/.test(q)){
    const aliases=[['drawdown',1],['maksimalt fall',1],['maks fall',1],['dsr',3],['deflated',3],['sharpe',2],['nettoavkastning',0],['kjøp og hold',4],['tilfeldig timing',5],['robusthet',6],['out-of-sample',7],['evalueringsdata',7],['slippage',8],['funding',8],['gebyr',8]];
    const term=aliases.find(([word])=>q.includes(word));
    if(term){const [name,definition]=MIE_GLOSSARY[term[1]];return answer(name+': '+definition,'MIEs ordliste · generell begrepsforklaring, ikke et rapportfunn');}
  }
  if(/flest handler|antall handler|hvor mange handler/.test(q)){
    const rules=d.data&&d.data.rules;
    if(rules&&typeof rules==='object'){
      const names={double_bottom:'Dobbel bunn',double_top:'Dobbel topp',inverse_head_shoulders:'Omvendt hode–skuldre',head_shoulders:'Hode–skuldre',symmetric_triangle:'Symmetrisk trekant'};
      const counts=Object.entries(rules).filter(([,r])=>Number.isFinite(r?.metrics?.trades)).map(([key,r])=>({name:names[key]||key,n:r.metrics.trades})).sort((a,b)=>b.n-a.n);
      if(counts.length)return answer(counts.map(x=>`${x.name}: ${x.n} handler.`).join('\n')+'\n\nFlest handler betyr ikke at en regel er best eller tilstrekkelig dokumentert.',d.source+' · rules.*.metrics.trades');
    }
    if(Number.isFinite(d.metrics?.Handler))return answer(`Rapporten teller ${d.metrics.Handler} handler etter motorens definisjon. Antallet alene beviser ikke at resultatet er pålitelig.`,source);
    return fallback();
  }
  if(/hvorfor|resultat|konklusjon|godkjent|kvalifisert/.test(q)){
    // The original summary is quoted for multi-rule reports, rather than inventing a common verdict reason.
    if(d.id==='btc-patterns')return answer('Utdrag fra den lagrede oppsummeringen:\n\n'+d.report.split('## Hva som faktisk')[0].trim(),(d.report_source||d.source)+' · innledning og vurderinger');
    const blocks=explainReport(d).filter(b=>b.title==='Kort fortalt'||b.title.startsWith('Hvorfor')||b.title==='Begrunnelse fra kilden');
    return answer(blocks.map(b=>b.text).join('\n\n'),source+' · dom og begrunnelser');
  }
  if(/risiko|fall|avkastning/.test(q)){
    const blocks=explainReport(d).filter(b=>(/risiko|fall/.test(q)&&b.title.startsWith('Risiko:'))||(/avkastning/.test(q)&&b.title.startsWith('Avkastning:')));
    if(blocks.length)return answer(blocks.map(b=>b.text).join('\n\n'),source+' · regneeksempler er illustrasjoner');
  }
  return fallback();
}
let chatReport=null;
function resetReportChat(d=null){
  chatReport=d;
  document.getElementById('reportChat').hidden=!d;
  document.getElementById('chatMessages').replaceChildren();
  document.getElementById('chatQuestion').value='';
  document.getElementById('chatContext').textContent=d?'Spørsmål om «'+d.name+'».':'';
}
function sendReportQuestion(question){
  if(!chatReport||!question.trim())return;
  const log=document.getElementById('chatMessages');
  const entry=document.createElement('article');entry.className='chat-entry';
  const h=document.createElement('h3');h.textContent='Du: '+question;
  const response=answerReportQuestion(chatReport,question);
  const p=document.createElement('p');p.textContent=response.text;
  const citation=document.createElement('small');citation.textContent='Kilde: '+response.source;
  entry.append(h,p,citation);log.append(entry);
  // Keep the current page bounded without storing questions elsewhere.
  while(log.children.length>10)log.firstElementChild.remove();
  document.getElementById('chatQuestion').value='';
}
document.getElementById('chatForm').addEventListener('submit',e=>{e.preventDefault();sendReportQuestion(document.getElementById('chatQuestion').value);});
for(const button of document.querySelectorAll('[data-question]'))button.addEventListener('click',()=>sendReportQuestion(button.dataset.question));
document.getElementById('clearChat').addEventListener('click',()=>resetReportChat(chatReport));
