# Optimizacija sekvenci

Implementirane su jednonitna, višenitna i višeprocesna Pareto pretraga sa razmjenom kandidata. Konačna mjerenja i poređenja su u [IZVJESTAJ.md](IZVJESTAJ.md), a svih 18 rezultata u [results/final](results/final).

## Okruženje i instalacija

Mjereno na 13th Gen Intel(R) Core(TM) i7-13650HX, 14 fizičkih jezgara / 20 logičkih procesora, Windows 11 (`Windows-11-10.0.26300-SP0`), Python 3.15.2 i NumPy 2.5.4, sa uključenim GIL-om. Ostalo je standardna Python biblioteka. Kod koristi sintaksu Python 3.10+; konačna serija je provjerena na navedenom okruženju.

```powershell
python -m pip install numpy==2.5.4
```

Sve komande pokretati iz foldera `prvi`: evaluator učitava `anchors.json` i `blosum62.json` iz trenutnog foldera. Formula evaluatora i njegovi podaci nisu mijenjani u konačnoj seriji.

## Pokretanje

Primjer jednonitnog P1 za prvo sjeme:

```powershell
python main.py --population-size 50 --max-initial-mutations 3 --batch-size 25 --mutation-lambda 2 --budget 40000 --seed 7183 --output results/P1_seed7183.json
```

Za P2 dodati `--threads 4`. Za P3 dodati `--processes 4 --threads 1 --migration-count 5 --exchange-interval 5 --topology ring`. Za različite lambda vrijednosti koristiti `--mutation-lambdas 0.5 1.5 2.5 3.5` umjesto `--mutation-lambda 2`.

Tačna komanda kompletne konačne serije P1–P6:

```powershell
python run_experiments.py --budget 40000 --population-size 50 --batch-size 25 --max-initial-mutations 3 --mutation-lambda 2 --process-lambdas 0.5 1.5 2.5 3.5 --seeds 7183 29461 85607 --threads 4 --migration-count 5 --exchange-interval 5 --alternative-exchange-interval 1 --second-topology all-to-all --timeout 300 --output-dir results/final
```

Postojeći izlaz istog imena se prepisuje; za novo mjerenje koristiti drugi `--output-dir`. `python main.py --help` prikazuje argumente. `run_experiments.py` pokreće eksperimente redom i pravi JSON, čitljive Markdown arhive, logove, `runs.json` i zbirnu tabelu.

| Parametar | Značenje |
|---|---|
| `--budget` | B, ukupan budžet uključujući inicijalizaciju; djeljiv sa P |
| `--population-size`, `--batch-size`, `--max-initial-mutations` | N, M i k |
| `--mutation-lambda` / `--mutation-lambdas` | ista vrijednost / po jedna vrijednost za svaki proces |
| `--threads`, `--processes` | T po lokalnom pool-u, P radnih procesa |
| `--migration-count`, `--exchange-interval` | K po susjedu, E lokalnih iteracija |
| `--topology` | `ring`, `star` ili `all-to-all` |
| `--seed`, `--output` | korijensko sjeme i izlazni fajl |
| `--timeout` | dodatni ukupni sigurnosni rok za procese, podrazumijevano 300 s |

G=100 ostaje konstanta u kodu. N≥2, M≥1, B≥1, 1≤k≤32, lambda≥0, T/P≥1, K≥0, E≥1. Timeout mora biti konačan i pozitivan. Početna sekvenca je fiksno `TEIYQAGSTPCNGVEGFNCYFPLQSYGFQPTN`. Greška argumenta prekida pokretanje; greška procesa čuva dostupne rezultate, označava nepotpunost i vraća izlazni kod 1.

## Pravila i strukture

Populacija i arhiva su liste kandidata; evidencija je rječnik po sekvenci. Inicijalizacija pravi original i jedinstvene mutante originala sa 1..k promjena. **Početno generisanje i evaluacije su sekvencijalni**, a u verzijama sa pool-ovima početno rangiranje koristi pool. Inicijalizacija ulazi u B i ukupno vrijeme, ali ne u prosjek redovnih iteracija.

Ukrštaju se dva različita roditelja unutrašnjim presjekom. Broj promjena je Poasonov; različite pozicije dobijaju drugo dozvoljeno slovo. Populacija/arhiva ostaju nepromijenjene dok se prikuplja do M lokalnih kandidata. Nakon spajanja bez duplikata prvi sloj postaje arhiva, a populacija se bira po rangu i razređenosti. Nulti raspon kriterijuma se preskače. Završava se potrošnjom B **ili** poslije G uzastopnih duplikata; djelimična iteracija se ipak ažurira.

## Niti, procesi i protokol

`run_threaded` koristi dva odvojena pool-a sa po T niti: prikupljanje i rangiranje. Jedan `Lock` štiti lokalni RNG, provjeru i rezervaciju sekvence, listu iteracije i brojače. Rezervisani kandidat se upisuje sa `scores=None` prije evaluacije. Evaluator je izvan zaključavanja; ocjene i završeni brojač upisuju se pod zaključavanjem. Brža nit nastavlja odmah. Glavna nit čeka sve evaluacije prije ažuriranja; samo provjere dominacije idu u drugi pool, ostala selekcija ostaje u glavnoj niti.

Svaki proces koristi `spawn`, lokalni budžet B/P, sopstvene strukture i jedan RNG izveden preko `SeedSequence(seed).spawn(P)`. P3–P6 koriste `run_threaded` sa T=1 po pool-u, što ima dodatni trošak i pri jednom radniku. Komunikacija između procesa koristi isključivo `multiprocessing.Queue`.

Poslije svake E-te iteracije šalje se do K kandidata arhive sa najvećom razređenošću; ako ih je manje, šalju se svi, a izjednačenje se razrješava sekvencom. Poruka sadrži pošiljaoca, iteraciju i `(sequence, scores, origin_process)` zapise. Prsten šalje i→(i+1) mod P, zvezda ima centar 0, svi-svima šalje svim drugim procesima.

Na početku iteracije primalac bez čekanja prazni dostupne poruke. Poznate sekvence odbacuje, nove evidentira sa ocjenama i uključuje u kraj te iteracije. Prijem ne troši B ni M, ne resetuje G i ne mijenja roditelje usred iteracije. Broj primljenih uključuje duplikate; prihvaćeno znači lokalno novu sekvencu, ne obavezno tačku arhive. Poslato broji kandidata za svakog primaoca.

Završne arhive i statistika prenose se posebnim Queue redovima, jer imaju promjenljivu veličinu i ne zahtijevaju fiksni format zajedničke memorije. Roditelj ih spaja, deduplikuje i uklanja dominirane istom dominacijom. Za isti kandidat sa više prijavljenih origin ID-jeva bira najmanji; lokalni `foreign_archive_size` broji tuđe porijeklo u svakoj lokalnoj arhivi.

Kad proces završi ili pogriješi, ostali nastavljaju. Roditelj odbacuje kasne poruke za završeni proces da pošiljaoci mogu završiti pražnjenje bafera. Nadzire poruke i izlazne kodove, a pomoćne daemon niti i ograničen timeout sprečavaju neograničeno čekanje. Poslato zato može biti veće od obrađenog primljenog. Ako neki završni izvještaj nedostaje, `evaluation_counts_complete=false`: broj prijavljenih evaluacija nije potpuna potrošnja svih procesa.

## Provjere i rezultati

```powershell
python test_sequential.py
python test_threaded.py
python test_processes.py
```

U konačnoj provjeri prošli su postojeći jednonitni testovi, 9 testova niti i 14 testova procesa. Pokriveni su Pareto postupci, mutacija/ukrštanje, duplikati, mali i nedjeljivi budžeti, oba završetka, nepromijenjena populacija, završavanje evaluacija prije ažuriranja, mali prostor sa mnogo niti, lokalni prijem bez B/M, topologije, spajanje, rani završetak primaoca i greške/nagli izlazak procesa. Arhiva tokom prikupljanja nije dostupna radnicima; nema zasebnog testa njenog snimka. Detalji i naknadna provjera svih 18 rezultata opisani su u izvještaju.

JSON čuva parametre, završnu i lokalne arhive, sva mjerenja i okruženje; Markdown po pokretanju čuva konačne sekvence/ocjene i pregled procesa. `origin_process` se prenosi pri migraciji. Hipervolumen koristi lo=(0,0,0), hi=(10,10,10), ref=(-0.1,-0.1,-0.1), jednako svuda.

Ukupno zidno vrijeme obuhvata inicijalizaciju, pretragu i rad sa procesima/pool-ovima. Ne obuhvata početni import roditelja, računanje metrika, ispis i izlazni JSON/Markdown; komunikacija i učitavanje podataka djece jesu uključeni. Lokalno `search_seconds` je pretraga prije završne poruke, a lokalno `total_seconds` opaženi životni vijek procesa. Prosjek iteracije ne uključuje inicijalizaciju i za više procesa je ponderisani prosjek svih lokalnih iteracija. Serija ima ista tri sjemena, jedno pokretanje po sjemenu, bez posebnog warm-up-a.

## Fajlovi

`model.py` — podaci; `pareto.py` — dominacija, rangovi, razređenost i izbor; `search.py` — lokalna pretraga; `processes.py` — procesi i razmjena; `metrics.py` — metrike; `main.py` — argumenti i izlaz; `run_experiments.py` — serija P1–P6; `test_*.py` — provjere; `IZVJESTAJ.md` — rezultati i zaključci.
