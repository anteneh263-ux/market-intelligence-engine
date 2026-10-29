# Hyperliquid BTC: teknisk dataprøve

Offentlige data hentet 29. oktober 2026 fra POST https://api.hyperliquid.xyz/info.
`candleSnapshot`: BTC, 1h, siste inntil 210 dager. `fundingHistory`: BTC,
samme periode, paginert med siste tidsstempel + 1 ms; inntil 20 sider.
De faktiske tidsstemplene, antall poster og SHA-256 er lagret i quality.json.
Ingen innlogging, betaling, handelsordre eller private nøkler ble brukt.

Dette er en separat rådatasnapshot for diagnostikk, ikke et MIE-evidenslager,
H-025-observasjoner, registrering eller en aktiv fremoverskuende test.
Resultateier er denne diagnostikkmappen. Ikke importer snapshoten til eksisterende
vitenskapelige lagre uten separat godkjent kontrakt.

Timeslys dekker omtrent 208 dager. Det er nok til en innledende SMA-50-prøve
med oppvarming, men ikke mange års robusthetstesting. Ukens nyeste uavsluttede
lys utelukkes ved analysen; råfilen bevarer API-responsen. Funding er kun
sammenholdt per UTC-time, ikke validert mot Vantas faktiske oppgjørstidspunkt.
OHLC beskriver ikke prisrekkefølgen inne i timen. En 2 % tapsstopp kan derfor
ikke antas utført til nøyaktig stoppris. Referansepris, spread, slippage og
Vantas kontoberegning må fortsatt kobles til en spesifisert simulator.

Status: data hentet og kvalitetssjekk kjørt. Strategivarianten med daglig
maks 10 % eksponering og 2 % tapsstopp er IKKE kjørt. Ingen lønnsomhet,
kvalifisering eller utbetaling er dokumentert. Ikke grunnlag for betalt deltakelse.

Vanta-kode undersøkt ved f860ec3a9ff6aeb91368018f0028a00718ff5b46:
position.py beregner realisert/urealisert PnL fra mengde og pris;
vali_config.py angir kvalifisering og terskler;
hl_funding_rate_manager.py bruker fundingHistory.
Full validator-ekvivalens er ikke verifisert.

Kilder:
- https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint
- https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data
- https://github.com/taoshidev/vanta-network

Reproduser kontrollen med scripts/check_hyperliquid_snapshot.py og eksplisitte
--candles, --funding og --asof-ms fra quality.json (konverter UTC til epoch ms).
