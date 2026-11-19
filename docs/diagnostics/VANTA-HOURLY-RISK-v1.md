# Separat SMA-50-prøve med timesbasert risikostyring

Historisk utforskende diagnostikk, VANTA-HOURLY-RISK-v1. Eier: denne rapporten og tilhørende JSON i docs/diagnostics. Ingen MIE-runtime-store, nye matriseversjoner, eksperimentregistreringer eller H-025-artefakter opprettes eller endres. Kildebase: 64fd855690c6d367379538f5d5c224aff1acd229.

## Regler bestemt før kjøring

SMA-50 på 50 foregående hele UTC-dagers sluttpriser. Første varme start er 25. mai 2026. Long når siste dags sluttpris er over gjennomsnittet, ellers cash. Daglig rebalansering ved UTC-midnatt til 10 % av post-fee egenkapitalen. Eksponeringen kan vokse mellom daglige justeringer, så dette er ikke et kontinuerlig hardt tak.

2 % stopp måles fra global høyeste porteføljeverdi ved timeslutt, inkludert startkapital. Signalet utføres ved neste times åpning, aldri til antatt stoppris. Etter stopp kreves et senere daglig cash-signal og deretter long-signal før ny inngang. Global topp nullstilles ikke. Timesluttstoppen kan gi større tap enn 2 % ved gap eller rask bevegelse.

Funding debiteres ved timebucketens start på tidligere posisjon og åpningspris, en eksplisitt forenkling. Kostnad: 3 bps spread + antatt 5 bps slippage per omsatt notional. Dette er ikke dokumentert faktisk utførelse eller Vantas eksakte konto-/fundingregnskap.

## Resultater 25. mai–29. oktober 2026

| Scenario | Avkastning | Største fall ved timeslutt | Antall stopp |
|---|---:|---:|---:|
| stop_zero_cost | 1.934 % | 0.820 % | 0 |
| stop_cost_funding | 1.624 % | 0.870 % | 0 |
| no_stop_cost_funding | 1.624 % | 0.870 % | 0 |

Ingen kontrollerte 5 % intradag-lav-proxy-, 5 % EOD-, 8 % EOD- eller 60-dagers inaktivitetsbrudd ble funnet. Daglige rebalanseringer er faktiske posisjonsjusteringer i modellen. Den observerte 2 % stoppen ble aldri truffet: samme resultat med og uten stopp dokumenterer ikke stoppens effekt.

Kvalifiseringens avkastningskrav, maksimal varighet, rangering og utbetaling er ikke implementert eller bestått. +1,62 % over hele perioden er ikke bevis på Vanta-kvalifisering. Kurver ville fortsatt etter plattformbrudd kun for diagnostikk. Ingen hovednettsdeltakelse anbefales på dette grunnlaget.

## Validering og begrensninger

12 isolerte tester utført og bestått etter korreksjon av ett kunstig testforløp: den opprinnelige fixturen hadde et daglig salgssignal før stoppen kunne trigges. Testen ble endret til et gap mens long-signalet fortsatt var aktivt. Ingen testresultater eller historikk er omskrevet. Testene dekker årsakstiming, neste-times stopp og lås, funding/kostnad, manglende funding, uavsluttede lys og tidligere skjermingsmatematikk. Full repo-suite, Vanta-validatorintegrasjon og testnett er ikke utført.

Bare én kort historisk periode. Ikke uavhengig urørt holdout, siden rådata nå er undersøkt. Times-OHLC kan ikke dokumentere intratime-prisrekkefølge eller utførelse. Fundingnotional og prisreferanse er forenklet. Ingen lønnsomhet, produksjonsverifikasjon eller fremtidig gyldighet er vist.

## Reproduser

    python scripts/vanta_hourly_risk_screen.py --candles docs/diagnostics/hyperliquid-snapshot-20261029/btc_1h.json --funding docs/diagnostics/hyperliquid-snapshot-20261029/btc_funding.json --asof-ms 1793303612522

    python -m unittest discover -s tests -p "test_vanta*screen.py" -v

Input-SHA-256 finnes i JSON. Offentlige datakilder og Vanta-kodeversjon er dokumentert i hyperliquid-snapshot-20261029/README.md. Dette er en separat metodologisk diagnostikk, ikke en oppdatering av eksisterende vitenskapelige resultater.
