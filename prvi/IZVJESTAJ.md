# Izvještaj o optimizaciji sekvenci

Implementirane su jednonitna, višenitna i višeprocesna pretraga sa razmjenom kandidata. Korišten je `oceni.py` sa github-a. U konačnoj seriji svih 18 pokretanja završilo je bez greške i potrošilo isti budžet od 40.000 evaluacija. P2 je bio sporiji od P1, dok je P3 bio brži od obje verzije. Različite vrijednosti mutacije i topologija svi-svima nisu povećale završni hipervolumen u odnosu na P3; češća razmjena u P6 nije donijela poboljšanje.

## 1. Problem i organizacija implementacije

Optimizuju se tri ocjene sintetičkog evaluatora `oceni.py`, pri čemu je veća vrijednost svake ocjene bolja. Početna sekvenca je `TEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTN`, dužine 32. Dozvoljeno je 20 standardnih slova `ACDEFGHIKLMNPQRSTVWY`; dužina se ne mijenja. Formula evaluatora i ulazni JSON podaci nisu mijenjani tokom mjerenja.

Populacija i Pareto arhiva su liste objekata `Candidate`, a evidencija je rječnik po sekvenci. Rječnik omogućava provjeru poznate sekvence bez prolaska kroz sve kandidate. Populacija služi izboru roditelja, arhiva čuva pronađene nedominirane kompromise, a evidencija zadržava i odbačene i trenutno evaluirane sekvence. Jednake ocjene različitih sekvenci nisu duplikati sekvence.

Početna populacija sadrži original i do N−1 jedinstvenih mutanata originala, sa po 1 do k promjena. **Generisanje i evaluacija početne populacije su sekvencijalni u svakoj lokalnoj pretrazi.** To je izbor implementacije, ne dodatni zahtjev postavke. U P2–P6 početno rangiranje koristi poseban pool. Početne evaluacije ulaze u budžet i ukupno vrijeme; inicijalizacija se ne računa kao redovna iteracija.

U redovnoj iteraciji dva različita roditelja biraju se iz nepromijenjene trenutne populacije. Presjek je unutrašnji, a broj mutiranih različitih pozicija izvlači se iz Poasonove raspodjele. Novo slovo mora biti drugačije od prethodnog. Nakon do M novih lokalnih evaluacija spajaju se populacija, prethodna arhiva i novi kandidati bez duplikata. Prvi Pareto sloj postaje arhiva; populacija se popunjava po rangu, uz prednost veće razređenosti u sloju koji ne staje. Kriterijum sa nultim rasponom preskače se.

Koriste se oba propisana uslova završetka: potrošen budžet ili G=100 uzastopnih poznatih sekvenci. Uspješna lokalna rezervacija resetuje G brojač. Duplikat ne troši evaluaciju. Nepotpuna posljednja iteracija ažurira arhivu nakon završetka svih već započetih evaluacija.

### Niti

P2 koristi dva odvojena `ThreadPoolExecutor` objekta kapaciteta T=4: jedan za generisanje/evaluaciju i drugi za Pareto rangiranje. Pod jednim `Lock`-om radnik provjerava uslove, koristi zajednički lokalni RNG, provjerava duplikat, upisuje kandidata sa `scores=None` u evidenciju i listu iteracije i rezerviše evaluaciju. Evaluator radi izvan zaključavanja; upis ocjena i završenih evaluacija ponovo je zaključan. Brži radnik nastavlja bez barijere nakon svakog kandidata. Glavna nit čeka sve radnike prije ažuriranja.

Pool za rangiranje provjerava nezavisno da li je svaki kandidat dominiran među preostalima. Slojevi se obrađuju redom; upis rangova, razređenost i selekcija ostaju u glavnoj niti. Zadržan je isti postupak traženja slojeva kao u P1. U najgorem slučaju ponovljeno traženje slojeva ima O(n³) poređenja; zato veća arhiva može povećati cijenu rangiranja. Pool-ovi se koriste kroz više iteracija.

### Procesi, poruke i završavanje

P3–P6 koriste `spawn`, četiri nezavisne lokalne pretrage i T=1 po lokalnom pool-u. Svaki proces poziva postojeći `run_threaded` sa jednim radnikom; to znači da i za T=1 ostaje trošak pool-ova. Svaki ima sopstvene strukture i budžet 10.000. Početnih evaluacija je zato ukupno 200, dok ih u P1/P2 ima 50; sve su uračunate u isti ukupni B. Procesi smiju nezavisno evaluirati istu sekvencu: deduplikacija evidencije je lokalna, ne globalna.

Jedan `default_rng` pravi se po lokalnoj pretrazi. Za procese se koristi `SeedSequence(seed).spawn(P)`, pa procesi imaju zasebne tokove. Sjemena su proizvoljno izabrana prije serije, bez izbora na osnovu rezultata. Isti korijenski seed ne obezbjeđuje identičan rezultat nove višeprocesne serije jer pristizanje poruka zavisi od raspoređivanja.

Nakon svake E-te završene iteracije šalje se najviše K kandidata sa najvećom razređenošću. Ako ih ima manje od K, šalju se svi. Pri izjednačenju odlučuje leksikografski redoslijed sekvence. Poruka sadrži pošiljaoca, iteraciju i zapise `(sequence, scores, origin_process)`. Prsten je usmjeren i→(i+1) mod P; u svi-svima svaki proces šalje preostalim trima. Implementirana je i zvezda sa centrom 0, ali nije korišćena kao druga topologija konačne serije.

Na početku lokalne iteracije `get_nowait` prazni trenutno dostupne poruke do `Empty`. Poznate sekvence se odbacuju, a nove se evidentiraju sa primljenim ocjenama i uključuju u ažuriranje iste iteracije. One ne pozivaju evaluator, ne troše B, ne ulaze u M, ne resetuju G i ne postaju roditelji usred iteracije.

Konačni rezultati prikupljaju se porukama preko posebnog `multiprocessing.Queue` reda po procesu, bez zajedničke memorije. Redovi prirodno prenose promjenljive arhive, stringove i statistiku; nije potrebno unaprijed određivati raspored i veličinu zajedničkog bafera. Roditelj spaja lokalne arhive, uklanja iste sekvence i dominirane kandidate istom funkcijom dominacije. Pri više prijavljenih porijekla iste sekvence deterministički bira najmanji origin ID; to nije dokaz globalno prvog pronalaska.

Kada proces završi, roditelj prazni i odbacuje kasne poruke za njega, da drugi procesi ne ostanu blokirani pri pražnjenju svojih bafera. Poslato broji predaju redu po svakom primaocu, primljeno obrađene kandidate uključujući duplikate, a prihvaćeno samo lokalno nove sekvence. Zato poslato i primljeno nisu nužno jednaki. Izvorni origin ID čuva se pri prosljeđivanju. Lokalni `foreign_archive_size` broji tačke lokalne arhive sa tuđim origin ID-jem; zbir preko procesa broji pojavljivanja, ne jedinstvene globalne tačke.

Roditelj nadzire završne poruke i izlazne kodove. Uhvaćene greške se prijavljuju porukom, a nagli izlazak bez izvještaja prepoznaje se nadzorom. Pomoćne niti za čitanje i pražnjenje redova postoje samo radi koordinacije. Dodatni sigurnosni timeout je 300 s za cijelo višeprocesno pokretanje, uz ograničena završna čekanja. To nije normalni optimizacioni uslov završetka. Pri grešci se čuvaju dostupni rezultati i označavaju nepotpuni brojači; u ovoj seriji nije bilo takvih slučajeva.

## 2. Okruženje i metod mjerenja

- Procesor: 13th Gen Intel(R) Core(TM) i7-13650HX; 14 fizičkih jezgara i 20 logičkih procesora.
- Sistem: `Windows-11-10.0.26300-SP0`.
- Python: `3.15.2 (tags/v3.15.2:5e58eaa, Oct 25 2026, 17:27:03) [MSC v.1951 64 bit (AMD64)]`.
- NumPy: `2.5.4`; ostali moduli su iz standardne biblioteke. GIL je uključen.

Mjereno je zidno vrijeme pomoću `perf_counter`. Glavni `total_seconds` obuhvata poziv pretrage: inicijalizaciju, rad i zatvaranje pool-ova, te u P3–P6 pokretanje/uvoz modula djece, komunikaciju, završavanje i završno spajanje arhiva. Parsiranje argumenata, početni import roditeljskog interpretera, računanje izlaznih metrika, ispis i zapis rezultata su izvan intervala. I/O redova i učitavanje JSON-a u novim dječjim interpreterima jesu unutar višeprocesnog vremena.

Vrijeme lokalne iteracije uključuje prijem, generisanje i evaluacije, ažuriranje i poziv slanja. Inicijalizacija je izostavljena iz tog prosjeka, ali nije izostavljena iz ukupnog vremena ili budžeta. Globalni prosjek je ponderisani prosjek svih lokalnih iteracija, ne trajanje sinhronizovane globalne generacije. Po procesu `search_seconds` mjeri lokalnu pretragu prije završne poruke, dok lokalni `total_seconds` mjeri interval od `Process.start` do roditeljevog opažanja izlaska, uz rezoluciju petlje nadzora.

Sva pokretanja izvršena su redom P1–P6, sa tri sjemena po konfiguraciji, bez međusobnog preklapanja eksperimenata. Nije bilo posebnog probnog pokretanja konačnih parametara niti ponavljanja radi izbora povoljnijeg rezultata. Izvršeno je jedno pokretanje po sjemenu, bez posebnog warm-up-a; testovi su pokrenuti prije serije. Pozadinski rad sistema, energetski profil i frekvencija procesora nisu kontrolisani. Tokom serije kod i JSON podaci nisu mijenjani.

## 3. Parametri konačne serije

| Parametar | Vrijednost |
|---|---|
| B | 40.000 ukupnih evaluacija |
| N / M / k | 50 / 25 / 3 |
| G | 100 uzastopnih duplikata, u kodu |
| K | 5 po primaocu |
| Sjemena | 7183, 29461, 85607 |
| Lambda za P1–P3 | 2,0 |
| Lambda za procese 0–3 u P4–P6 | 0,5; 1,5; 2,5; 3,5 |
| E | 5 u P3–P5; 1 u P6 |
| Sigurnosni timeout | 300 s, nije dostignut |

Različite lambda vrijednosti imaju prosjek 2,0, jednak P3, pa promjena nije istovremeno promjena prosječnog parametra. Manja lambda daje oprezniju, veća širu pretragu. N i M su umjereni da se ne preoptereti rangiranje; E=5 odgovara razmjeni poslije do 125 novih lokalnih evaluacija, a E=1 poslije do 25. Parametri nisu mijenjani nakon uvida u konačne rezultate.

| Oznaka | P × T | Lambda | Topologija | E |
|---|---|---|---|---:|
| P1 | 1 × 1 | 2 | — | — |
| P2 | 1 × 4 | 2 | — | — |
| P3 | 4 × 1 | sve 2 | prsten | 5 |
| P4 | 4 × 1 | različite | prsten | 5 |
| P5 | 4 × 1 | različite | svi-svima | 5 |
| P6 | 4 × 1 | različite | prsten | 1 |

Hipervolumen je računat postupkom iz postavke, za maksimizaciju. Fiksne granice normalizacije su lo=(0,0,0), hi=(10,10,10), prema dostavljenom evaluatoru i referentnim ocjenama. Referenca (-0,1,-0,1,-0,1) preuzeta je iz primjera postavke. To su naše fiksne postavke metrike za cijelu seriju, ne minimumi i maksimumi pojedinačne arhive. Veći hipervolumen je bolji; veća arhiva sama to ne garantuje.

## 4. Rezultati svih pokretanja

Vrijeme je u sekundama; prosjek lokalne iteracije u milisekundama. Sva pokretanja imaju `budget_exhausted`, po 40.000 započetih i završenih evaluacija, bez grešaka. P1/P2 imaju 1.598 iteracija. Svaki proces P3–P6 ima 398 iteracija i 10.000 evaluacija (ukupno 1.592 lokalne iteracije).

| Pokretanje | Sjeme | Ukupno s | Evaluacije | Arhiva | Hipervolumen | Prosjek iteracije ms |
|---|---:|---:|---:|---:|---:|---:|
| [P1](results/final/P1_seed7183.md) | 7183 | 3.166372 | 40000 | 5 | 0.562387416 | 1.9691 |
| [P1](results/final/P1_seed29461.md) | 29461 | 3.250299 | 40000 | 5 | 0.568525000 | 2.0206 |
| [P1](results/final/P1_seed85607.md) | 85607 | 3.237158 | 40000 | 5 | 0.562387416 | 2.0134 |
| [P2](results/final/P2_seed7183.md) | 7183 | 10.369104 | 40000 | 5 | 0.562387416 | 6.4735 |
| [P2](results/final/P2_seed29461.md) | 29461 | 8.798952 | 40000 | 5 | 0.568525000 | 5.4886 |
| [P2](results/final/P2_seed85607.md) | 85607 | 9.117886 | 40000 | 5 | 0.562387416 | 5.6895 |
| [P3](results/final/P3_seed7183.md) | 7183 | 2.518410 | 40000 | 5 | 0.568525000 | 4.8127 |
| [P3](results/final/P3_seed29461.md) | 29461 | 2.203474 | 40000 | 5 | 0.568525000 | 4.2748 |
| [P3](results/final/P3_seed85607.md) | 85607 | 2.186630 | 40000 | 5 | 0.568525000 | 4.2419 |
| [P4](results/final/P4_seed7183.md) | 7183 | 2.339510 | 40000 | 5 | 0.568525000 | 4.4251 |
| [P4](results/final/P4_seed29461.md) | 29461 | 2.475120 | 40000 | 5 | 0.568525000 | 4.5696 |
| [P4](results/final/P4_seed85607.md) | 85607 | 2.324451 | 40000 | 5 | 0.568525000 | 4.2763 |
| [P5](results/final/P5_seed7183.md) | 7183 | 2.436221 | 40000 | 5 | 0.568525000 | 4.6263 |
| [P5](results/final/P5_seed29461.md) | 29461 | 2.516075 | 40000 | 5 | 0.568525000 | 4.7970 |
| [P5](results/final/P5_seed85607.md) | 85607 | 2.373954 | 40000 | 5 | 0.568525000 | 4.3226 |
| [P6](results/final/P6_seed7183.md) | 7183 | 2.533558 | 40000 | 5 | 0.563462440 | 4.8532 |
| [P6](results/final/P6_seed29461.md) | 29461 | 2.388439 | 40000 | 5 | 0.568525000 | 4.5157 |
| [P6](results/final/P6_seed85607.md) | 85607 | 2.471881 | 40000 | 5 | 0.568525000 | 4.4416 |

### Prosjeci preko tri sjemena

Standardna devijacija je uzoračka preko tri pokretanja; ona nije interval pouzdanosti niti procjena čiste vremenske varijanse pri fiksnom sjemenu.

| Konfiguracija | Prosjek s | SD vremena s | Prosjek HV | SD HV |
|---|---:|---:|---:|---:|
| P1 | 3.217943 | 0.045143 | 0.564433277 | 0.003543536 |
| P2 | 9.428647 | 0.829924 | 0.564433277 | 0.003543536 |
| P3 | 2.302838 | 0.186881 | 0.568525000 | 0.000000000 |
| P4 | 2.379694 | 0.082984 | 0.568525000 | 0.000000000 |
| P5 | 2.442083 | 0.071242 | 0.568525000 | 0.000000000 |
| P6 | 2.464626 | 0.072831 | 0.566837480 | 0.002922870 |

### Rasponi ocjena konačne arhive

| Konfiguracija | Sjeme | Prva ocjena min–max | Druga ocjena min–max | Treća ocjena min–max |
|---|---:|---|---|---|
| P1 | 7183 | 2–10 | 1.8–9 | 3–10 |
| P1 | 29461 | 2–10 | 1.8–9 | 3–10 |
| P1 | 85607 | 2–10 | 1.8–9 | 3–10 |
| P2 | 7183 | 2–10 | 1.8–9 | 3–10 |
| P2 | 29461 | 2–10 | 1.8–9 | 3–10 |
| P2 | 85607 | 2–10 | 1.8–9 | 3–10 |
| P3 | 7183 | 2–10 | 1.8–9 | 3–10 |
| P3 | 29461 | 2–10 | 1.8–9 | 3–10 |
| P3 | 85607 | 2–10 | 1.8–9 | 3–10 |
| P4 | 7183 | 2–10 | 1.8–9 | 3–10 |
| P4 | 29461 | 2–10 | 1.8–9 | 3–10 |
| P4 | 85607 | 2–10 | 1.8–9 | 3–10 |
| P5 | 7183 | 2–10 | 1.8–9 | 3–10 |
| P5 | 29461 | 2–10 | 1.8–9 | 3–10 |
| P5 | 85607 | 2–10 | 1.8–9 | 3–10 |
| P6 | 7183 | 1.79–10 | 1.8–9 | 3–10 |
| P6 | 29461 | 2–10 | 1.8–9 | 3–10 |
| P6 | 85607 | 2–10 | 1.8–9 | 3–10 |

## 5. Poređenja i tumačenje

Ubrzanje je odnos prosječnih vremena prethodne i nove konfiguracije; vrijednost veća od 1 znači brže izvršavanje. Promjena HV računa se iz prosječnih hipervolumena.

| Poređenje | Novo/prethodno vrijeme | Ubrzanje | Promjena prosječnog HV |
|---|---:|---:|---:|
| P2 prema P1 | 2.930 | 0.341 | +0.000% |
| P3 prema P2 | 0.244 | 4.094 | +0.725% |
| P4 prema P3 | 1.033 | 0.968 | +0.000% |
| P5 prema P4 | 1.026 | 0.974 | +0.000% |
| P6 prema P4 | 1.036 | 0.966 | -0.297% |

**P2 prema P1.** Prosječno vrijeme raste sa 3,218 na 9,429 s, odnosno P2 je oko 2,93 puta sporiji. Za svako sjeme završna arhiva i ocjene su potpuno iste, pa nema promjene završnog kvaliteta. Evaluator i dominacija su pretežno kratke Python petlje; uz uključen GIL niti ne daju očekivano paralelno izvršavanje tog dijela računanja. Dodati su zaključavanje, poslovi pool-a i prikupljanje rezultata, a generisanje je serijalizovano zbog zajedničkog RNG-a i rezervacija. Ovi faktori objašnjavaju usporenje, ali nisu odvojeno profilisani, pa im ne pripisujemo tačne pojedinačne udjele.

**P3 prema P2.** P3 traje prosječno 2,303 s, odnosno 4,09 puta je brži od P2. U odnosu na P1 ubrzanje je 1.40, što pokazuje da odnos prema sporom P2 nije isto što i idealno četvorostruko ubrzanje. Procesi mogu računati na različitim jezgrima, ali plaćaju `spawn`, učitavanje podataka i redove poruka. Prosječni HV je veći za oko 0,725%; P3 je u sva tri pokretanja dostigao 0,568525, dok P1/P2 u dva nisu. Ovo poređenje istovremeno mijenja organizaciju pretrage: četiri populacije i slučajna toka, lokalne budžete i razmjenu. Zato razliku kvaliteta ne pripisujemo samo paralelizmu ili samo razmjeni.

**P4 prema P3.** Različite lambda vrijednosti zadržale su isti HV 0,568525 u sva tri pokretanja. Prosječno vrijeme je veće za oko 3,34%. U ovoj seriji nema dokaza poboljšanja konačne arhive heterogenim mutacijama; ista srednja lambda ne znači iste sekvence ni istu cijenu generisanja.

**P5 prema P4.** Svi-svima povećava prosječan broj poslatih kandidata sa 1.397,67 na 3.999,00 (oko 2,86 puta), a završni HV ostaje isti. Svaka razmjena ima tri primaoca umjesto jednog; broj kandidata nije tačno trostruk jer veličine lokalnih arhiva kroz pretragu nisu identične. Vrijeme raste za oko 2,62%, a broj lokalno novih primljenih kandidata ostaje gotovo isti. Za ovaj problem dodatna komunikacija nije donijela bolji završni kvalitet.

**P6 prema P4.** Razmjena svake iteracije umjesto svake pete povećava prosječno slanje sa 1.397,67 na 6.936,33 kandidata, oko 4,96 puta. Prosječno vrijeme raste za oko 3,57%. Više je prihvaćenih kandidata, ali prosječni HV je niži za oko 0,297%, zbog sjemena 7183 (HV 0,563462440 umjesto 0,568525). Češća razmjena može ranije promijeniti populacije i putanju pretrage; ovdje veća aktivnost razmjene nije značila bolje rješenje. Jedan slabiji ishod nije dokaz da je E=1 uopšteno lošiji.

Male vremenske razlike P3–P6 ne treba tumačiti kao siguran efekat jednog parametra: postoje samo tri sjemena, bez ponovljenih mjerenja istog sjemena i bez kontrole pozadinskog opterećenja. Sva pokretanja su završila sa istim brojem evaluacija, pa ovdje nema razlike zbog ranijeg dostizanja G. Jednak broj poziva ipak ne znači jednak broj globalno različitih sekvenci: različiti procesi mogu ponavljati evaluacije. Taj međuprocesni broj ponavljanja nije posebno instrumentiran.

## 6. Komunikacija i podaci po procesu

Prikazani zbir tuđih tačaka predstavlja zbir pojavljivanja u lokalnim arhivama. Ista sekvenca može biti prisutna kod više procesa; to nije broj jedinstvenih tačaka zajedničke arhive.

| Konfiguracija | Sjeme | Poslato kandidata | Primljeno | Prihvaćeno novih | Poruka poslato/primljeno | Tuđe tačke zbir |
|---|---:|---:|---:|---:|---|---:|
| P3 | 7183 | 1071 | 971 | 62 | 316/296 | 13 |
| P3 | 29461 | 1463 | 1448 | 103 | 316/313 | 15 |
| P3 | 85607 | 1358 | 1338 | 67 | 316/312 | 14 |
| P4 | 7183 | 1378 | 1333 | 62 | 316/307 | 14 |
| P4 | 29461 | 1372 | 1302 | 70 | 316/302 | 15 |
| P4 | 85607 | 1443 | 1388 | 75 | 316/305 | 15 |
| P5 | 7183 | 4005 | 3790 | 80 | 948/905 | 14 |
| P5 | 29461 | 3816 | 3651 | 54 | 948/915 | 15 |
| P5 | 85607 | 4176 | 3941 | 72 | 948/901 | 15 |
| P6 | 7183 | 6187 | 5872 | 65 | 1592/1529 | 13 |
| P6 | 29461 | 7075 | 6780 | 78 | 1592/1533 | 15 |
| P6 | 85607 | 7547 | 7107 | 117 | 1592/1504 | 15 |

### Lokalni rezultati P3–P6

Svaki red ima 10.000 započetih i završenih evaluacija i 398 iteracija. `Vrijeme` uključuje opaženi životni vijek procesa; lokalno vrijeme same pretrage i svi rasponi sačuvani su u JSON-u. Za P1/P2 jedini lokalni rezultat odgovara njihovom prikazu u prethodnoj tabeli.

| Konfiguracija/sjeme | Proces | Vrijeme s | Prosjek iteracije ms | Arhiva | Poslato | Primljeno | Prihvaćeno | Tuđe tačke |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| P3/7183 | 0 | 2.365470 | 5.0139 | 5 | 282 | 259 | 16 | 4 |
| P3/7183 | 1 | 2.205831 | 4.6310 | 5 | 254 | 242 | 15 | 4 |
| P3/7183 | 2 | 2.371685 | 5.0971 | 5 | 276 | 254 | 15 | 1 |
| P3/7183 | 3 | 2.126736 | 4.5088 | 5 | 259 | 216 | 16 | 4 |
| P3/29461 | 0 | 2.077248 | 4.3028 | 5 | 363 | 365 | 27 | 5 |
| P3/29461 | 1 | 2.065346 | 4.3263 | 5 | 361 | 363 | 25 | 3 |
| P3/29461 | 2 | 2.012487 | 4.1763 | 5 | 374 | 346 | 20 | 4 |
| P3/29461 | 3 | 2.050690 | 4.2938 | 5 | 365 | 374 | 31 | 3 |
| P3/85607 | 0 | 2.001613 | 4.1183 | 5 | 336 | 327 | 20 | 4 |
| P3/85607 | 1 | 2.038277 | 4.2552 | 5 | 343 | 336 | 18 | 4 |
| P3/85607 | 2 | 2.059443 | 4.3207 | 5 | 337 | 343 | 18 | 4 |
| P3/85607 | 3 | 2.022446 | 4.2735 | 5 | 342 | 332 | 11 | 2 |
| P4/7183 | 0 | 2.029757 | 4.1718 | 5 | 346 | 298 | 9 | 2 |
| P4/7183 | 1 | 2.092755 | 4.3641 | 5 | 345 | 346 | 18 | 4 |
| P4/7183 | 2 | 2.116623 | 4.4717 | 5 | 344 | 345 | 14 | 3 |
| P4/7183 | 3 | 2.200326 | 4.6927 | 5 | 343 | 344 | 21 | 5 |
| P4/29461 | 0 | 2.042147 | 4.2307 | 5 | 346 | 275 | 5 | 1 |
| P4/29461 | 1 | 2.133051 | 4.5039 | 5 | 346 | 346 | 20 | 5 |
| P4/29461 | 2 | 2.124262 | 4.4911 | 5 | 335 | 346 | 24 | 5 |
| P4/29461 | 3 | 2.337942 | 5.0526 | 5 | 345 | 335 | 21 | 4 |
| P4/85607 | 0 | 2.000452 | 4.1124 | 5 | 361 | 317 | 7 | 2 |
| P4/85607 | 1 | 1.957077 | 4.0226 | 5 | 359 | 351 | 20 | 4 |
| P4/85607 | 2 | 2.069563 | 4.3514 | 5 | 361 | 359 | 24 | 4 |
| P4/85607 | 3 | 2.169298 | 4.6186 | 5 | 362 | 361 | 24 | 5 |
| P5/7183 | 0 | 2.098657 | 4.3872 | 5 | 993 | 909 | 15 | 0 |
| P5/7183 | 1 | 2.087007 | 4.3459 | 5 | 981 | 903 | 16 | 4 |
| P5/7183 | 2 | 2.251284 | 4.8152 | 5 | 1020 | 980 | 23 | 5 |
| P5/7183 | 3 | 2.304681 | 4.9569 | 5 | 1011 | 998 | 26 | 5 |
| P5/29461 | 0 | 2.188616 | 4.5888 | 5 | 951 | 880 | 9 | 2 |
| P5/29461 | 1 | 2.206116 | 4.6629 | 5 | 942 | 898 | 13 | 4 |
| P5/29461 | 2 | 2.246835 | 4.7972 | 5 | 948 | 926 | 16 | 5 |
| P5/29461 | 3 | 2.375673 | 5.1393 | 5 | 975 | 947 | 16 | 4 |
| P5/85607 | 0 | 2.002576 | 4.0991 | 5 | 1035 | 952 | 9 | 3 |
| P5/85607 | 1 | 1.958957 | 4.0223 | 5 | 1026 | 935 | 17 | 3 |
| P5/85607 | 2 | 2.119836 | 4.4333 | 5 | 1056 | 1015 | 22 | 4 |
| P5/85607 | 3 | 2.218889 | 4.7356 | 5 | 1059 | 1039 | 24 | 5 |
| P6/7183 | 0 | 2.179759 | 4.5302 | 5 | 1525 | 1289 | 8 | 0 |
| P6/7183 | 1 | 2.305373 | 4.9055 | 5 | 1555 | 1525 | 17 | 4 |
| P6/7183 | 2 | 2.264123 | 4.8384 | 5 | 1543 | 1515 | 17 | 4 |
| P6/7183 | 3 | 2.379320 | 5.1389 | 5 | 1564 | 1543 | 23 | 5 |
| P6/29461 | 0 | 2.032850 | 4.1895 | 5 | 1745 | 1481 | 4 | 1 |
| P6/29461 | 1 | 2.079226 | 4.3547 | 5 | 1777 | 1745 | 25 | 5 |
| P6/29461 | 2 | 2.208014 | 4.6990 | 5 | 1777 | 1777 | 26 | 5 |
| P6/29461 | 3 | 2.246994 | 4.8198 | 5 | 1776 | 1777 | 23 | 4 |
| P6/85607 | 0 | 1.944882 | 4.0161 | 5 | 1876 | 1452 | 10 | 1 |
| P6/85607 | 1 | 1.979324 | 4.1021 | 5 | 1888 | 1876 | 34 | 4 |
| P6/85607 | 2 | 2.158843 | 4.6071 | 5 | 1891 | 1888 | 36 | 5 |
| P6/85607 | 3 | 2.324273 | 5.0412 | 5 | 1892 | 1891 | 37 | 5 |

## 7. Provjere ispravnosti

Prije konačne serije prošle su postojeće provjere u `test_sequential.py`, svih 9 testova u `test_threaded.py` i svih 14 testova u `test_processes.py`. Logovi su u folderu konačnih rezultata.

| Oblast | Provjereno ponašanje |
|---|---|
| Jedna nit | Dominacija, slojevi i jednake ocjene, razređenost, mutacija i ukrštanje, deduplikacija, izbor populacije/arhive, mali budžet uključujući B=1, oba uslova završetka i ponovljivost sjemena |
| Niti | Konkurentne rezervacije u malom prostoru sa 16 niti, bez ponovljenih evaluacija; ograničenje M i B kada nisu djeljivi brojem niti; nepromjenjivost populacije; završene ocjene prije ažuriranja u glavnoj niti; djelimična iteracija; brži radnik nastavlja; greška evaluatora se prenosi |
| Rangiranje | Isti slojevi i rangovi sekvencijalno i paralelno, uključujući izjednačene ocjene; bez promjene pravila izbora |
| Procesi | B/P, topologije za male P, poznati/dominirani/nedominirani prijem, prijem bez trošenja B i M ili promjene roditelja usred iteracije, spajanje kao direktno rangiranje istog skupa |
| Završavanje | Rani završetak primaoca dok poruke stižu, uhvaćena greška, nagli izlazak bez rezultata i ograničen timeout |

Arhiva se ne prosljeđuje radnicima za prikupljanje i postojeći kandidati se tamo ne mijenjaju. Njena nepromjenjivost u toj fazi obezbijeđena je organizacijom koda; zaseban test sa snimkom same arhive nije dodat, za razliku od direktne provjere populacije.

Poslije serije provjereno je svih 18 konfiguracija, sjemena, brojača, lokalnih budžeta i broja iteracija. Svaka konačna sekvenca ponovo je ocijenjena i ocjene se poklapaju; završne arhive nemaju duplikate ni dominirane tačke i odgovaraju spajanju lokalnih arhiva. Provjereni su rasponi, porijeklo i prosjeci. Hipervolumen je nezavisno provjeren i formulom uključivanja–isključivanja zapremina presjeka kvadara, pored postojeće slojevite implementacije. Te provjere su izvan mjerenja i ne mijenjaju budžet izmjerenih pretraga.

## 8. Ograničenja i naredne provjere

Tri sjemena i po jedno vremensko mjerenje nisu dovoljni za statistički pouzdanu generalizaciju. Redoslijed konfiguracija nije randomizovan. Za čvršće zaključke potrebni su više sjemena, ponavljanja istog sjemena, kontrolisan energetski profil i pozadinsko opterećenje, te drugi B, N i M.

Nisu posebno profilisani evaluator, zaključavanje, rangiranje i serijalizacija. Zato objašnjenja troškova odgovaraju strukturi implementacije i opaženim rezultatima, ali ne predstavljaju izolovana mjerenja svakog uzroka. Nije mjeren tok HV kroz iteracije, pa jednak završni HV ne govori koja konfiguracija je ranije došla do dobrog fronta. Najveći opaženi HV nije dokaz globalnog optimuma.

Prijem korisnih tuđih kandidata pokazuje da razmjena radi, ali ne dokazuje njenu korist u odnosu na nezavisne procese bez razmjene. Za to bi trebalo dodati kontrolu sa K=0, pri istim sjemenima, budžetima i lambda vrijednostima. To dodatno poređenje nije dio ove konačne serije P1–P6.

## 9. Reprodukcija i predati rezultati

Komanda se pokreće iz foldera `prvi`:

```powershell
python run_experiments.py --budget 40000 --population-size 50 --batch-size 25 --max-initial-mutations 3 --mutation-lambda 2 --process-lambdas 0.5 1.5 2.5 3.5 --seeds 7183 29461 85607 --threads 4 --migration-count 5 --exchange-interval 5 --alternative-exchange-interval 1 --second-topology all-to-all --timeout 300 --output-dir results/final
```

Novo izvršavanje istom putanjom prepisuje rezultate; za dodatne serije treba izabrati drugi izlazni folder. [`runs.json`](results/final/runs.json) sadrži stvarne komande i izlazne kodove, a pojedinačni Markdown fajlovi iz tabele daju sve sekvence i tri ocjene zajedničke konačne arhive. Odgovarajući JSON fajlovi sadrže i kompletne lokalne arhive i metrike svakog procesa. Sačuvani su logovi, [`hardware.json`](results/final/hardware.json) i automatska [`summary.md`](results/final/summary.md). Ovaj izvještaj je tumačenje tih izmjerenih podataka, bez zamjene neuspjelih ili slabijih pokretanja.
