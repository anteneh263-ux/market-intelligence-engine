# MIE chart patterns — TradingView-indikator

Fil: `mie_chart_patterns_v1_1.pine` (Pine Script v6).

## Ta den i bruk

1. Åpne Binance **BTCUSDT perpetual** i TradingView, normalt `BINANCE:BTCUSDT.P`.
   Velg vanlige candles og **4H**. Ikke bruk Heikin Ashi, spot eller en annen børs
   når du sammenligner med MIE. Kontroller kontraktnavnet i instrumentbeskrivelsen.
2. Åpne Pine Editor, opprett en ny indikator og erstatt innholdet med Pine-filen.
3. Lagre og velg «Add to chart». Eventuelle kompilatorfeil må løses før bruk.
4. Sett grafens tidssone til UTC for sammenligning. Standard detektorstart er
   2020-02-20 UTC, som i MIE. Last nok historikk før utsnittet du undersøker.

## Hva grafen viser

- Grønn etikett: bullish mønster bekreftet ved candle-slutt.
- Oransje etikett: bearish mønster bekreftet ved candle-slutt.
- Mønsterlinjer: historiske vendepunkter, tegnet FØRST når signalet bekreftes.
  Linjer bakover i grafen betyr ikke at signalet var kjent på disse tidspunktene.
- Stiplet linje: halslinje eller trekantgrense. Rød prikket linje: stoppnivå ved signal.
- Liten NEXT-markør: neste candle, altså tidligste mulighet for inngang. Ingen
  ordre fylles av indikatoren, og markørens plassering er ikke en utførelseskurs.
- Tabell: antall detektorsignaler i historikken TradingView har lastet. Den er ikke
  begrenset til MIEs testperiode, og er ikke en oversikt over utførte handler.

Reglene er faste: strenge 5/5-pivoter, samme ATR beregnet som SMA av true range,
samme toleranser, trendkrav, utløp og kryssingsregler som Python-versjonen.
Oppdatering av pivottilstand og signaler skjer bare når `barstate.isconfirmed`.
Det finnes ingen `request.security`, lookahead eller negative plot-offsets.
Når maksimalt antall tegninger nås, fjernes de eldste tegningene fra visningen;
det endrer ikke signalseriene. En endring av data/startdato kan endre historikken.

## Fast sammenligningsliste

`mie_chart_patterns_v1_1_reference.csv` inneholder alle 21 Python-detektorsignaler
i testperioden 2021–2024, inkludert stop, retning, pivotdatoer og om MIE tok handelen.
Disse signalene er allerede undersøkt historikk, ikke en ny test av lønnsomhet.

Begynn med disse candle-ÅPNINGSTIDENE i UTC (etiketten blir kjent fire timer senere):

| Mønster | Åpningstid for bekreftelsescandle | Signal kjent ved slutt |
|---|---|---|
| Dobbel bunn | 2021-09-02 04:00 | 2021-09-02 08:00 |
| Dobbel topp | 2021-05-04 12:00 | 2021-05-04 16:00 |
| Omvendt hode–skuldre | 2021-06-14 12:00 | 2021-06-14 16:00 |

MIE fant ingen hode–skuldre eller symmetriske trekanter med disse reglene i
testperioden. Alle fem har positive syntetiske tilfeller i Python-testene.
Bruk Bar Replay: stopp før bekreftelsescandlen, gå én candle videre, og kontroller
at etiketten først kommer ved bekreftet slutt. Den skal ikke flyttes til bunnen.

De fem `MIE ...`-plottene i Data Window viser +1/-1 på signaldagen og 0 ellers.
Hvis TradingView-abonnementet tillater eksport av chart data, kan disse kolonnene
sammenlignes med referansefilen, på candle-åpningstid. Samme OHLC-data og tilstrekkelig
oppvarmingshistorikk er nødvendig for eksakt samsvar.

## Verifikasjonsstatus

- Implementert og manuelt sammenholdt med Python-detektorens regler.
- Referanselisten er generert med den eksisterende MIE-detektoren fra de samme
  lagrede perpetual-dataene som studien brukte. Ingen nye strategivarianter kjørt.
- Kompilert, lagret privat og kjørt i TradingView 2026-10-27 på
  `BINANCE:BTCUSDT.P`, vanlige candles, 4H, UTC. Reservert variabelnavn `text`
  ble erstattet med `labelText`; ingen detektorregler ble endret.
  Tabellen ble visuelt bekreftet på grafen uten synlig kjørefeil.
  Egen layout: https://www.tradingview.com/chart/cNpsPKy7/
- Pine/Python-samsvar på historiske tidsstempler og faktisk Bar Replay-oppførsel
  er fortsatt ikke bekreftet. Python-testene er ikke Pine-tester. Tabelltallene
  gjelder innlastet TradingView-historikk, ikke nødvendigvis MIEs testperiode.
- Dette er en indikator, ikke en Pine-strategi: posisjonstilstand, risiko/mål,
  tidsutgang, gebyrer, funding og resultatregnskap er ikke implementert her.

Offisiell dokumentasjon om candle-bekreftelse:
https://www.tradingview.com/pine-script-docs/concepts/bar-states/
