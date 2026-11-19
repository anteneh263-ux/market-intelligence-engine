# Separat SMA-200-test: lavere eksponering

Historisk, utforskende screening. Ingen endring av H-025 eller eksisterende evidens.
Eksponeringen settes ved inngang, og antall Bitcoin holdes fast til utgang. Resten beholdes som kontanter uten rente. Eksponeringen kan derfor vokse etter kursoppgang.

De samme ni overlappende startperiodene brukes; dette er ikke uavhengige tester. Data slutter 8. juli 2026.

| Kostnader | Eksponering | Perioder over 5 % EOD-fall | Over 8 % EOD-fall | Inaktivitet |
|---|---:|---:|---:|---:|
| zero_cost | 10% | 6/9 | 4/9 | 9/9 |
| zero_cost | 20% | 8/9 | 7/9 | 9/9 |
| zero_cost | 100% | 8/9 | 8/9 | 9/9 |
| illustrative_cost | 10% | 7/9 | 4/9 | 9/9 |
| illustrative_cost | 20% | 8/9 | 7/9 | 9/9 |
| illustrative_cost | 100% | 8/9 | 8/9 | 9/9 |

## Tolkning og begrensninger

Lavere eksponering reduserer utslagene, men endrer ikke handelstidspunktene eller inaktiviteten. Ingen variant kan erklæres kvalifisert. Avkastningskrav, 61 handelsdager, rangering og utbetaling er ikke validert.

8 bps per side og 10,95 % årlig carry på posisjonens daglige åpningsverdi er et illustrativt scenario, ikke observerte Vanta-kostnader. Daglig OHLC og fast antall enheter gjenskaper ikke Vantas posisjonsberegning, funding eller intradagkontroll. Kurver fortsetter etter regelbrudd og er ikke avkastning på overlevende kontoer. Ingen stop-loss eller raskere signaler er lagt til.

Implementert og kjørt: separat screening, 6 isolerte kontrolltester bestod. Full repo-suite, Vanta-integrasjon, testnett og hovednett er ikke kjørt. Ingen lønnsomhet eller fremtidig gyldighet er bevist.

Kilder for terskler: https://github.com/taoshidev/vanta-network/blob/main/docs/miner.md og https://github.com/taoshidev/vanta-network/blob/main/README.md (lest 29. oktober 2026; ikke låst til commit).

Resultateier og lagring: denne separate diagnostikkrapporten og tilhørende JSON under docs/diagnostics; ingen runtime-store opprettes. Reproduseres med screen(..., exposure=.1) og screen(..., exposure=.2) fra scripts/vanta_sma200_screen.py på de samme startdatoene og kostnadene som VANTA-SMA200-SCREEN-v1.
