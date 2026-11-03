'use strict';
// Deterministic explanation of saved evidence; no model, new verdict or prediction.
function explainReport(d) {
  const blocks=[];
  const add=(title,text)=>blocks.push({title,text});
  const num=(x,digits=1)=>Number(x).toLocaleString('nb-NO',{maximumFractionDigits:digits});
  const finite=x=>typeof x==='number'&&Number.isFinite(x);
  const verdicts={
    'Supported':'Strategien bestod kravene i denne undersøkelsen. Det er støtte i disse dataene, ikke en garanti for at du vil tjene penger fremover.',
    'Not Supported':'Undersøkelsen ga ikke tilstrekkelig støtte for at strategien har den fordelen vi lette etter. Det betyr ikke nødvendigvis at den tapte penger i perioden.',
    'Graveyard':'Strategien ble lagt til side fordi den brøt et viktig krav i denne testen. Det er en dom over dette oppsettet og disse dataene, ikke alle mulige varianter av ideen.',
    'Inconclusive':'Datagrunnlaget eller resultatene er for usikre til å gi en klar konklusjon.',
    'Ingen kvalifiserte':'Ingen av de fem mønsterreglene kvalifiserte i denne undersøkelsen. Noen hadde positiv historisk avkastning, men det var ikke nok til å bestå kravene.'
  };
  add('Kort fortalt',verdicts[d.verdict]||'Denne dommen har foreløpig ingen forenklet forklaring. Les originalrapporten nedenfor.');
  const m=d.metrics||{};
  if(finite(m.Nettoavkastning)) {
    const r=m.Nettoavkastning;
    add('Avkastning: hva skjedde i perioden?',`Den samlede nettoavkastningen var ${num(r*100)} %. «Netto» betyr etter kostnadene som modellen tok med. Det er avkastningen over hele testperioden, ikke per år. Som et rent regneeksempel ville 10 000 kr blitt til ${num(10000*(1+r),0)} kr ved akkurat denne avkastningen. Skatt og kostnader utenfor modellen kan ikke antas å være med.`);
  }
  if(finite(m['Maks fall'])) {
    const dd=m['Maks fall'];
    add('Risiko: hvor mye kunne verdien falle underveis?',`Rapportens største fall fra en topp var ${num(dd*100)} %. Et tilsvarende fall fra en kontoverdi på 10 000 kr ville gitt ${num(10000*(1-dd),0)} kr igjen. Dette er et regneeksempel på fallet, ikke kontoens faktiske start- eller sluttsaldo. Positiv sluttavkastning kan altså skjule store tap underveis.`);
  }
  if(finite(m.Handler))add('Hvor mye erfaring bygger resultatet på?',`Rapporten teller ${num(m.Handler,0)} handler etter motorens definisjon. Flere handler gir mer å undersøke, men handler kan være avhengige av hverandre. Antallet alene beviser ikke at resultatet er pålitelig.`);
  const translations={
    'failed: deflated Sharpe clears bar':'Resultatet klarte ikke kravet i den statistiske kontrollen som forsøker å ta hensyn til at mange ideer kan ha blitt prøvd.',
    'failed: OOS Sharpe positive':'Strategien klarte ikke kravet om positiv risikojustert avkastning i evalueringsdelen.',
    'failed: beats buy-and-hold (risk-adjusted)':'Strategien slo ikke kravet mot å kjøpe og holde, når avkastningen ble vurdert opp mot svingningene.',
    'failed: beats matched random-timing':'Strategien slo ikke kravet mot kontrollstrategier med tilfeldig valgte handelstidspunkter.',
    'failed: robust across majority of conditions':'Fordelen holdt ikke i nok av de undersøkte betingelsene, for eksempel ved endrede kostnader.'
  };
  for(const reason of d.reasons||[]) {
    if(translations[reason])add('Hvorfor bestod den ikke?',translations[reason]);
    else {
      const match=reason.match(/^OOS max drawdown ([\d.]+)% exceeds gate ([\d.]+)%$/);
      if(match)add('Hvorfor ble den lagt til side?',`Maksimalt fall i evalueringsdelen var ${num(Number(match[1]))} %. Testen tillot høyst ${num(Number(match[2]))} %. Derfor ble tapsgrensen brutt.`);
      else if(d.id!=='btc-patterns')add('Begrunnelse fra kilden',reason);
    }
  }
  if(d.id==='btc-patterns')add('Hvorfor er ikke en positiv prosent nok?','Studien krevde minst 30 avsluttede handler per regel. Dobbel bunn hadde bare 10. Hode–skuldre og trekant ga ingen handler. Resultatene gjelder separate kontoer og skal ikke summeres. Se originalrapporten for hvert mønster og begrensningene i sammenligningen.');
  add('Hva kan du konkludere med?', 'Dette er en historisk undersøkelse av bestemte regler. Du kan bruke den til å forstå resultatet og svakhetene ved oppsettet. Den forteller ikke hva markedet vil gjøre i morgen eller hvor mye du vil tjene.');
  return blocks;
}
const MIE_GLOSSARY=[
 ['Nettoavkastning','Hvor mye verdien endret seg etter kostnadene som beregningen inkluderer. Samlet avkastning er ikke det samme som årlig avkastning.'],
 ['Maksimalt fall (drawdown)','Det største registrerte fallet fra en tidligere topp til en senere bunn. Sier noe annet enn resultatet ved slutten av perioden.'],
 ['Sharpe','Et mål på avkastning sett opp mot svingningene i avkastningen. Sammenlign bare tall med samme tidsenhet og beregningsmåte.'],
 ['DSR (Deflated Sharpe)','En statistisk kontroll av Sharpe-resultatet som tar hensyn til blant annet utvelgelse blant forsøk og fordelingens form. Kvaliteten avhenger av forutsetningene og forsøksregnskapet. 0,90 betyr ikke 90 % sjanse for å tjene penger.'],
 ['Kjøp og hold','En referanse der man kjøper et aktivum og beholder det. En aktiv strategi bør vurderes opp mot et relevant enkelt alternativ.'],
 ['Tilfeldig timing','Kontrollstrategier som velger handelstidspunkter tilfeldig innenfor bestemte regler. Brukes for å undersøke om resultatet kan skyldes tilfeldigheter.'],
 ['Robusthet','Om resultatet holder under flere rimelige betingelser, i stedet for bare ett spesielt oppsett.'],
 ['Evalueringsdata / out-of-sample','Data satt av til evaluering i den aktuelle metoden. Dette betyr ikke automatisk at historikken aldri har vært sett eller brukt tidligere.'],
 ['Gebyrer, slippage og funding','Gebyr er handelsavgiften. Slippage er forskjellen mellom antatt og oppnådd pris. Funding er periodiske betalinger mellom posisjoner i visse derivater. Bare modellens inkluderte kostnader er trukket fra.']
];
function renderExplanation(d){
  const target=document.getElementById('plainContent');target.replaceChildren();
  for(const b of explainReport(d)){const h=document.createElement('h3'),p=document.createElement('p');h.textContent=b.title;p.textContent=b.text;target.append(h,p);}
  const glossary=document.getElementById('glossary');glossary.replaceChildren();
  for(const [term,meaning] of MIE_GLOSSARY){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=term;dd.textContent=meaning;glossary.append(dt,dd);}
}

// Research triage only: original verdicts remain unchanged.
function recommendNextStep(d) {
  const reasons=Array.isArray(d.reasons)?d.reasons.filter(x=>typeof x==='string'):[];
  const fallback={recommendation:'Utilstrekkelig grunnlag',why:'Rapporten gir ikke en begrunnelse som denne veiledningen kan tolke sikkert.',action:'Les originalrapporten og avklar hvilke vurderingskriterier som er dokumentert.',missing:'En tydelig begrunnelse og dokumenterte kriterier for videre vurdering.'};
  const drawdown=reasons.find(x=>/^OOS max drawdown ([\d.]+)% exceeds gate ([\d.]+)%$/.test(x));
  if(d.verdict==='Graveyard'&&drawdown){
    const [,actual,limit]=drawdown.match(/^OOS max drawdown ([\d.]+)% exceeds gate ([\d.]+)%$/);
    return {recommendation:'Legg dette oppsettet til side',why:`Rapporten viser et maksimalt fall på ${actual.replace('.',',')} %, over testens grense på ${limit.replace('.',',')} %.`,action:'Dokumenter når og under hvilke forhold det store fallet oppstod i den lagrede rapporten, før en eventuell ny idé formuleres.',missing:'Et begrunnet nytt oppsett med en egen hypotese og vurderingskriterier fastsatt før evaluering.'};
  }
  if(d.verdict==='Not Supported'){
    const checks=[
      ['failed: robust across majority of conditions','Resultatet bestod ikke robusthetskravet.','Undersøk hvilke av de allerede rapporterte betingelsene som svekket resultatet, og oppsummer den viktigste svakheten.','Dokumentasjon på at fordelen holder under flere rimelige betingelser.'],
      ['failed: beats matched random-timing','Resultatet bestod ikke sammenligningen med tilfeldig handelstiming.','Gå gjennom den lagrede sammenligningen med tilfeldig timing og dokumenter hvor strategien skiller seg fra kontrollene.','Tilstrekkelig støtte for en fordel utover tilfeldig timing.'],
      ['failed: beats buy-and-hold (risk-adjusted)','Resultatet bestod ikke den risikojusterte sammenligningen med kjøp og hold.','Sammenlign risiko og avkastning med kjøp og hold i den eksisterende rapporten.','Dokumentert merverdi mot den valgte referansen.'],
      ['failed: OOS Sharpe positive','Resultatet bestod ikke kravet om positiv risikojustert avkastning i evalueringen.','Undersøk hvilke rapporterte perioder og kostnader som bidro til det svake resultatet.','Støtte for positiv risikojustert avkastning i evalueringsdelen.'],
      ['failed: deflated Sharpe clears bar','Resultatet bestod ikke den statistiske kontrollen av Sharpe.','Kontroller forsøksregnskapet og forutsetningene for den statistiske vurderingen i rapporten.','Tilstrekkelig statistisk støtte gitt antallet prøvde ideer.']
    ];
    const match=checks.find(([reason])=>reasons.includes(reason));
    if(match)return {recommendation:'Undersøk videre som forskningsidé',why:match[1],action:match[2],missing:match[3]+' Alle øvrige feilede krav i originalrapporten må også avklares.'};
  }
  if(d.verdict==='Supported')return {recommendation:'Forbered en uavhengig evaluering',why:'Den lagrede dommen er Supported: oppsettet bestod kravene i denne undersøkelsen.',action:'Dokumenter et nytt evalueringsoppsett med låste regler og kriterier før nye resultater undersøkes.',missing:'Dokumentasjon på om resultatet holder i en ny evaluering. Historisk støtte dokumenterer ikke fremtidig inntjening.'};
  if(d.verdict==='Ingen kvalifiserte'&&reasons.length)return {recommendation:'Avklar hvorfor ingen kvalifiserte',why:'Rapportens dom er Ingen kvalifiserte.',action:'Gå gjennom begrunnelsene for hver regel i originalrapporten og skill mellom for lite datagrunnlag og krav som faktisk feilet.',missing:'Tilstrekkelig datagrunnlag og oppfylte vurderingskriterier for minst én regel.'};
  return fallback;
}
function renderRecommendation(d){
  const r=recommendNextStep(d),target=document.getElementById('recommendationContent');
  target.replaceChildren();
  for(const [label,text] of [['Anbefaling',r.recommendation],['Hvorfor',r.why],['Neste handling',r.action],['Hva mangler?',r.missing]]){
    const h=document.createElement('h3'),p=document.createElement('p');h.textContent=label;p.textContent=text;target.append(h,p);
  }
  document.getElementById('recommendation').hidden=false;
}
