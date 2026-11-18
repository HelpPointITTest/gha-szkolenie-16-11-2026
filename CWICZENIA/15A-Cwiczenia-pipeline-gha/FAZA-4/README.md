# FAZA-4 — Terraform i deploy aplikacji na DigitalOcean

## Cel ćwiczenia

Rozszerz pipeline z [FAZY-3](../FAZA-3/README.md). Środowisko `test` nadal
działa jako kontener na dotychczasowym self-hosted runnerze. Dla `dev` i `prod`
utwórz po jednej VM DigitalOcean i udostępnij WWW oraz API pod adresami
`http://<IP_VM>/`, bez konfigurowania domeny.

Kod Terraform jest gotowy. Twoim zadaniem jest skonfigurowanie GitHub Actions,
sekretów i runnera oraz uruchomienie pipeline; nie musisz pisać zasobów
Terraform od początku. Terraform wygeneruje również oddzielne pary kluczy
SSH dla `dev` i `prod` — nie przygotowujesz ich ręcznie.

| Przygotowany plik lub katalog | Zastosowanie |
| --- | --- |
| [15A-FAZA4-pogoda.yaml](../../../.github/workflows/15A-FAZA4-pogoda.yaml) | Gotowy workflow w `.github/workflows/` |
| [terraform/](terraform/) | Wspólny kod infrastruktury dla `dev` i `prod` |
| [scripts/prepare_job.py](scripts/prepare_job.py) | Sprawdzenie i zachowanie ścieżek, workspace oraz wejść przebiegu |
| [scripts/read_release.py](scripts/read_release.py) | Sprawdzenie metadanych obrazu i publicznych wyników Terraform |
| [scripts/deploy_remote.py](scripts/deploy_remote.py) | Uruchomienie kontenera na VM przez SSH |

Joby infrastruktury działają na jednym self-hosted runnerze i wykonują
Terraform, który tworzy zasoby przez API DigitalOcean. Deploy uruchamia
aplikację przez SSH z tego runnera na VM. Jest to wykonanie przez SSH,
bez Ansible. Build, test i publikację obrazu realizują joby GitHub Actions.

**Dopiero uruchomienie joba `apply` przez słuchacza tworzy płatne zasoby
DigitalOcean.** Samo przygotowanie plików lub `terraform validate` ich nie tworzy.

## Krok 1. Sprawdź układ środowisk

| Environment | Miejsce działania | Tag obrazu | Adres aplikacji |
| --- | --- | --- | --- |
| `test` | Obecny self-hosted runner | `faza3-test` | `http://<IP_RUNNERA>:18093/` |
| `dev` | Osobny Droplet DigitalOcean | `faza4-dev` | `http://<IP_VM_DEV>/` |
| `prod` | Osobny Droplet DigitalOcean | `faza4-prod` | `http://<IP_VM_PROD>/` |

Wspólne repozytorium obrazu to `piotrskoska/python_pogoda`. Login i nazwę obrazu
możesz zastąpić własnymi w Repository variables.

Pipeline zachowuje jeden build i jeden rzeczywisty test CI. Publikacja używa
`matrix` dla `test`, `dev`, `prod`; infrastruktura i zdalny deploy używają
macierzy `dev`, `prod`. `test` nie otrzymuje Dropleta ani stanu Terraform.
Na VM port hosta `80` kieruje do Nginxa na `8080` wewnątrz kontenera.

## Krok 2. Przygotuj self-hosted runner

1. W **Settings → Actions → Runners** sprawdź, czy Twój runner Linux jest
   dostępny jako **Idle**.
2. Dodaj mu etykietę **`pogoda-state`**. Przypisz ją dokładnie jednemu runnerowi,
   na którego dysku ma pozostać stan Terraform.
3. Sprawdź, czy konto usługi runnera ma dostęp do Dockera i poleceń `git`,
   `python3`, `ssh`, `ssh-keygen`, `curl` oraz `unzip`.
4. Dopuść połączenia wychodzące HTTPS do GitHub, rejestru Terraform,
   DigitalOcean i Docker Hub, HTTP do aplikacji na VM oraz SSH do VM na `22`.
5. Ustal **publiczny IPv4, z którego runner wychodzi do Internetu**. Jeśli
   działa za NAT, potrzebujesz adresu NAT, a nie lokalnego `192.168.x.x`.
   Użyjesz go jako `FAZA4_SSH_ALLOWED_CIDR`, zwykle z maską `/32`.

Joby infrastruktury mają:

```yaml
runs-on: [self-hosted, linux, pogoda-state]
```

Etykieta kieruje kolejne joby na ten sam komputer. Samo `[self-hosted, linux]`
nie wystarczyłoby po dodaniu kolejnego runnera. Build i test nadal korzystają
z `ubuntu-latest`.

## Krok 3. Przygotuj trwały katalog stanu

Zaloguj się na runnera i wykonaj polecenia **jako konto usługi runnera**,
np. `student`. To jednorazowe przygotowanie katalogu na hoście:

```bash
install -d -m 0700 "$HOME/terraform-state/pogoda-faza4"
touch "$HOME/terraform-state/pogoda-faza4/.faza4-state-root"
chmod 0600 "$HOME/terraform-state/pogoda-faza4/.faza4-state-root"
```

Jeżeli katalog domowy konta runnera to `/home/student`, wartością
`FAZA4_STATE_ROOT` będzie `/home/student/terraform-state/pogoda-faza4`.
W GitHub wpisz pełną ścieżkę. Wartości `~` i `$HOME` nie są tam automatycznie
rozwijane przez powłokę.

Katalog musi leżeć na trwałym dysku, poza checkoutem repozytorium, `_work`
i `RUNNER_TEMP`. Plik `.faza4-state-root` oznacza świadomie przygotowaną
lokalizację. Workflow sprawdza go przed rozpoczęciem pracy.

Stan jest rozdzielony przez repozytorium, nazwę słuchacza i workspace:

```text
/home/student/terraform-state/pogoda-faza4/
├── .faza4-state-root
└── <repository_id>/
    └── jan-kowalski/
        ├── workspaces/
        │   ├── dev/terraform.tfstate
        │   └── prod/terraform.tfstate
        └── runs/<run_id>/...
```

`<repository_id>` i `<run_id>` uzupełnia GitHub Actions. Nie wpisujesz ich
w zmiennych. Backend `local` przechowuje stan na dysku i używa blokady
systemowej. [Dokumentacja local backend](https://developer.hashicorp.com/terraform/language/backend/local).

**Pliki stanu i zapisane plany zawierają prywatne klucze SSH wygenerowane
przez Terraform.** Dlatego katalog ma prawa `0700`, a plików nie wolno
drukować w logach, dodawać do Git ani uploadować jako artefaktów GitHub.
Oznaczenie outputu jako `sensitive` ukrywa go w zwykłym podglądzie, ale
nie szyfruje lokalnego stanu ani planu. Dostęp do runnera i kopii zapasowych
oznacza również dostęp do tych kluczy.

## Krok 4. Dodaj Repository variables

W **Settings → Secrets and variables → Actions → Variables** ustaw:

| Repository variable | Wartość początkowa | Znaczenie |
| --- | --- | --- |
| `DOCKERHUB_USERNAME` | `piotrskoska` | Konto publikujące obrazy |
| `DOCKER_IMAGE_NAME` | `python_pogoda` | Repozytorium obrazu |
| `FAZA4_DEPLOY_BRANCH` | `main` | Gałąź dopuszczona do publikacji i deploya |
| `FAZA4_WEATHER_CITY` | `Warsaw` | Miasto wspólnego testu CI |
| `FAZA4_STATE_ROOT` | `/home/student/terraform-state/pogoda-faza4` | Pełna ścieżka przygotowana w kroku 3; dostosuj do konta runnera |
| `FAZA4_STUDENT_NAME` | `jan-kowalski` | Twoje imię i nazwisko małymi literami, bez polskich znaków i ze spacją zastąpioną myślnikiem |
| `FAZA4_REGION` | `fra1` | Region DigitalOcean dostępny na koncie |
| `FAZA4_DROPLET_SIZE` | `s-1vcpu-1gb` | Rozmiar VM dostępny w tym regionie |
| `FAZA4_SSH_ALLOWED_CIDR` | `<PUBLICZNY_IPV4_RUNNERA>/32` | Rzeczywisty adres wyjściowy runnera z maską |

`203.0.113.10/32` to przykład zapisu CIDR, a nie gotowa wartość Twojego runnera.
Zastąp także `jan-kowalski` swoim identyfikatorem. Nazwy VM będą miały postać
`jan-kowalski-pogoda-f4-dev-<6_znaków>` oraz
`jan-kowalski-pogoda-f4-prod-<6_znaków>`. Losowy sufiks jest generowany jeden
raz i zachowywany w stanie; zwykły kolejny przebieg go nie zmienia.
Nazwy rejestrowanych kluczy publicznych oraz firewalli również zawierają
identyfikator słuchacza, środowisko i ten sufiks.

Nie twórz nowych sieci na potrzeby ćwiczenia. Kod używa istniejącej domyślnej
VPC wybranego regionu. Konto musi mieć ją dostępną; w razie jej braku wybierz
z prowadzącym region z gotową siecią.

Po pierwszym wdrożeniu nie zmieniaj `FAZA4_STATE_ROOT` ani
`FAZA4_STUDENT_NAME` jako sposobu wykonania kolejnego deploya — wskazałoby
to inny stan. Zmiana regionu, nazwy lub klucza może wymagać odtworzenia zasobów;
oceniaj wtedy wynik `plan`.

## Krok 5. Dodaj Repository secrets

W **Settings → Secrets and variables → Actions → Secrets** ustaw:

| Repository secret | Wartość |
| --- | --- |
| `DIGITALOCEAN_TOKEN` | Przekazany przez prowadzącego token API DigitalOcean z uprawnieniami do infrastruktury tego ćwiczenia |
| `DOCKERHUB_TOKEN` | Token Docker Hub z prawem publikacji i pobierania obrazu |
| `OPENWEATHER_API_KEY` | Aktywny, prawdziwy klucz API OpenWeatherMap |

Nie zapisuj wartości tokenów w YAML, Terraform, Markdown ani w repozytorium.
Token DigitalOcean jest dostępny providerowi jako `DIGITALOCEAN_TOKEN`,
bez przekazywania w `tfvars`.
[Konfiguracja providera](https://github.com/digitalocean/terraform-provider-digitalocean/blob/main/docs/index.md).

Token musi pozwalać odczytywać, tworzyć, aktualizować i usuwać Droplety,
firewalle i klucze SSH oraz odczytywać potrzebne informacje o regionach,
obrazach, rozmiarach i istniejącej sieci. Przy tokenie z ograniczonym zakresem
prowadzący powinien dopasować uprawnienia do tych zasobów. Token tylko
do odczytu nie wykona `apply`.
[Zakresy API DigitalOcean](https://docs.digitalocean.com/reference/api/scopes/).

Klucz pogodowy na poziomie repozytorium jest nadal wymagany: wspólny test CI
nie ma GitHub Environment i nie odczyta samego sekretu z `dev` lub `prod`.

## Krok 6. Skonfiguruj GitHub Environments

W **Settings → Environments** wykorzystaj `test`, `dev` i `prod`. Jeżeli ich
nie ma, utwórz je przez **New environment**. Zachowaj istniejące reguły dostępu
i dopuść gałąź wskazaną w `FAZA4_DEPLOY_BRANCH`.

W Environment `test` zachowaj zmienne FAZY-3:

| Environment variable | `test` |
| --- | --- |
| `FAZA3_IMAGE_TAG` | `faza3-test` |
| `FAZA3_APP_PORT` | `18093` |
| `FAZA3_CONTAINER_NAME` | `pogoda_api_faza3_test` |
| `FAZA3_WEATHER_CITY` | `Warsaw` |

W Environments `dev` i `prod` dodaj:

| Environment variable | `dev` | `prod` |
| --- | --- | --- |
| `FAZA4_IMAGE_TAG` | `faza4-dev` | `faza4-prod` |
| `FAZA4_WEATHER_CITY` | `Warsaw` | `Warsaw` |

Dla VM nie ustawiasz zmiennych portu, nazwy kontenera, IP ani ścieżki workspace.
Port HTTP to `80`, nazwa kontenera wynika ze środowiska, a IP zwraca Terraform.

Opcjonalnie ustaw różne `DOCKERHUB_TOKEN` i `OPENWEATHER_API_KEY`
w Environment `test`, `dev`, `prod`, nadpisując sekrety repozytorium.
Token DigitalOcean jest potrzebny tylko jobom infrastruktury `dev` i `prod`.
Klucze SSH wygeneruje Terraform; nie dodawaj sekretu `DO_SSH_PRIVATE_KEY`.
Istniejące reguły zatwierdzania Environment mogą wstrzymywać joby; pipeline
ich nie omija.

## Krok 7. Sprawdź automatyczne generowanie kluczy SSH

1. W gotowym Terraform znajdź zasób `tls_private_key` używający `ED25519`.
2. Każdy workspace otrzyma własną parę kluczy. Terraform zarejestruje
   publiczną część w DigitalOcean i przypisze ją właściwej VM.
3. Nie uruchamiaj ręcznie `ssh-keygen`, nie wgrywaj kluczy do DigitalOcean
   i nie twórz GitHub Secret z prywatnym kluczem.
4. Zachowaj stan każdego workspace: przechowuje zarówno identyfikatory
   infrastruktury, losowy sufiks nazw, jak i wygenerowaną parę kluczy.

Zwykły kolejny przebieg wykorzystuje istniejące klucze ze stanu. Job zdalnego
deploya odczytuje wyłącznie nazwany, wrażliwy output `ssh_private_key_openssh`
bezpośrednio do tymczasowego pliku `0600`; po deployu usuwa ten plik.
Nie wykonuj `terraform output -json` dla wszystkich outputów: taka komenda
mogłaby ujawnić klucz również pomimo oznaczenia `sensitive`.

Klucz prywatny pozostaje w chronionym stanie i może być również zawarty
w zapisanym planie. Czyszczenie tymczasowego pliku SSH nie usuwa go ze stanu,
ponieważ następny deploy ma ponownie skorzystać z tej samej pary.

## Krok 8. Sprawdź workflow w katalogu GitHub Actions

1. Pozostaw katalogi `terraform` i `scripts` w tej FAZIE-4.
2. Otwórz gotowy [workflow](../../../.github/workflows/15A-FAZA4-pogoda.yaml).
   Plik znajduje się już w `.github/workflows/15A-FAZA4-pogoda.yaml`.
3. Zatwierdź materiał ćwiczenia i plik workflow w swoim repozytorium,
   a następnie wyślij na gałąź `main` lub skonfigurowaną gałąź ćwiczenia.
   Aby pojawił się **Run workflow**, plik z `workflow_dispatch` powinien być
   obecny na domyślnej gałęzi repozytorium.
4. W **Actions** wyłącz wcześniejsze workflow FAZ 1–3 przez
   **… → Disable workflow**, jeśli ćwiczenia nie mają już działać równolegle.
   Ich filtry również reagują na zmiany aplikacji i mogłyby wykonywać kolejne
   publikacje lub deploye `test`.

GitHub odczytuje workflow z `.github/workflows/`. Edytuj bezpośrednio ten plik;
kod Terraform, skrypty i instrukcja pozostają w `FAZA-4`.

## Krok 9. Zachowaj wspólny build, cache i rzeczywisty test

1. `build` buduje aplikację z
   `CWICZENIA/15A-Cwiczenia-pipeline-gha/pogoda_python_flusk` na `ubuntu-latest`.
2. Używa cache warstw BuildKit z GitHub Actions i stałym scope FAZY-4.
3. Nie otrzymuje klucza pogody ani tokenu DigitalOcean. Zapisuje obraz jako
   artefakt dla dalszych jobów.
4. `test` ładuje ten obraz i uruchamia kontener z prawdziwym
   `OPENWEATHER_API_KEY`, przekazanym dopiero do `docker run`.
5. Sprawdza WWW i API, wykonując prawdziwe zapytanie o pogodę.
6. Po teście usuwa kontener CI, także przy błędzie.

Nie zastępuj rzeczywistego testu samym sprawdzeniem, czy obraz się zbudował.
Zasady cache są takie jak w [FAZIE-2](../FAZA-2/README.md).

## Krok 10. Opublikuj przetestowany obraz dla trzech Environments

Job publikacji ma matrix z `test`, `dev`, `prod` oraz pole
`environment: ${{ matrix.environment }}`.

1. Każdy wariant pobiera ten sam przetestowany obraz.
2. Odczytuje tag: `FAZA3_IMAGE_TAG` dla `test`, `FAZA4_IMAGE_TAG` dla `dev` i `prod`.
3. Loguje się do Docker Hub przez `DOCKERHUB_TOKEN` i publikuje bez przebudowy.
4. Zapisuje oddzielne metadane środowiska z digestem `sha256`.

Deploy uruchomi obraz po digescie, a nie po ruchomym tagu. Oddzielne artefakty
unikają nadpisywania wspólnego outputu przez warianty macierzy.
[Matrix i artefakty w dokumentacji GitHub](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations).

## Krok 11. Wykonaj `init` jako osobny job

Job infrastruktury `init` działa przez matrix dla `dev` i `prod`, na runnerze
`pogoda-state` i z właściwym GitHub Environment.

1. Udostępnia Terraform `1.17.2` przez `hashicorp/setup-terraform@v3`.
2. Sprawdza katalog stanu i przygotowuje trwałe pliki robocze bieżącego
   przebiegu poza checkoutem.
3. Korzysta z tego samego kodu z checkoutu i przygotowuje zmienne niesekretne:
   środowisko, identyfikator słuchacza, region, rozmiar VM oraz CIDR SSH.
4. Konfiguruje backend `local`, wskazując `workspace_dir` na trwały katalog
   `workspaces` tego repozytorium i słuchacza.
5. Wykonuje `terraform init` oraz `terraform workspace select -or-create`
   dla `dev` albo `prod`.
6. Pierwsze `init` pobiera przypięte wersje providerów i tworzy lockfile.
   Pipeline zachowuje `.terraform.lock.hcl` przy trwałym `TF_DATA_DIR` danego
   środowiska i odtwarza go po checkoutach następnych jobów. Dzięki temu
   czyszczenie checkoutu nie usuwa informacji potrzebnych do `validate`,
   `plan` i `apply`.

Workspace Terraform i GitHub Environment to dwa mechanizmy. Environment
wybiera konfigurację i sekrety joba; workspace wybiera stan tego samego kodu
Terraform. W tym ćwiczeniu nazwy odpowiadają sobie. Operacje infrastruktury
na workspace `default` są blokowane.

`TF_DATA_DIR` jest oddzielny dla środowiska i przebiegu, w `runs/<run_id>/...`,
a nie w checkoutowym `.terraform`. Kolejne joby wracają do tej samej
zainicjalizowanej konfiguracji.

## Krok 12. Wykonaj `validate` jako osobny job

1. Job `validate` startuje po sukcesie `init`.
2. Ponownie udostępnia tę samą wersję Terraform; ustawienie ścieżki polecenia
   jest wykonywane w każdym jobie.
3. Otwiera ten sam katalog roboczy i `TF_DATA_DIR` swojego środowiska.
4. Sprawdza wybrany workspace i wykonuje `terraform validate`.

Nie przesyła stanu ani `.terraform` jako artefaktu GitHub. Pliki pozostają
na trwałym dysku jednego przypisanego runnera.

## Krok 13. Wykonaj `plan` jako osobny job

1. Job `plan` zależy od sukcesu `validate`.
2. Używa tego samego kodu, workspace i pliku stanu.
3. Otrzymuje token w `DIGITALOCEAN_TOKEN`.
4. Wykonuje `terraform plan` i zapisuje plan na dysku runnera, w katalogu
   bieżącego przebiegu.
5. Przejrzyj w logu planowane zasoby i nazwy VM.

Pierwszy plan pokaże infrastrukturę wybranego środowiska, generowanie pary
kluczy SSH, rejestrację klucza publicznego, firewall i zasób losowego sufiksu.
Kod nie tworzy VPC i nie zarządza
infrastrukturą `test`. Planu nie uploadujemy do GitHub jako artefaktu;
kolejny job wykorzystuje lokalny plik właściwego przebiegu i środowiska.

## Krok 14. Wykonaj `apply` jako osobny job

1. Job `apply` startuje po sukcesie `plan`.
2. Sprawdza workspace i wykonuje `terraform apply` dla **zapisanego planu**.
3. Terraform tworzy lub uzgadnia parę kluczy SSH, VM, klucz publiczny
   zarejestrowany w DigitalOcean oraz firewall.
4. Zapisuje stan niezależnie dla `dev` i `prod`.
5. Odczytuje `public_ip`, `droplet_id` i `app_url` do dalszego deploya.

Cloud-init instaluje `docker.io`, `python3`, `curl` i certyfikaty CA,
a następnie włącza Dockera. Nie zawiera obrazu aplikacji ani sekretów.

Cały workflow ma wspólną grupę concurrency dla repozytorium oraz
`cancel-in-progress: false`, aby kolejny przebieg nie zmienił stanu pomiędzy
`plan` a `apply` pierwszego. Nie uruchamiaj równolegle ręcznych poleceń
Terraform poza pipeline.

Macierze mają wspólne zależności: kolejny etap czeka na sukces obu wariantów
poprzedniego etapu. Błąd infrastruktury `dev` może zatrzymać również dalszy
etap `prod`. `fail-fast: false` pozwala zakończyć uruchomione warianty, ale
nie usuwa tej zależności. Nie jest to sekwencyjna promocja dev → prod.

## Krok 15. Zachowaj deploy `test`

Job deploya `test` nadal:

1. Działa na self-hosted runnerze, w GitHub Environment `test`.
2. Odczytuje `FAZA3_APP_PORT`, `FAZA3_CONTAINER_NAME`, `FAZA3_WEATHER_CITY`.
3. Pobiera swój opublikowany obraz po digescie.
4. Usuwa poprzedni `pogoda_api_faza3_test`, jeżeli istnieje.
5. Uruchamia nowy na `18093:8080` i testuje WWW oraz prawdziwe API.
6. Czyści tymczasowe dane logowania Docker Hub także po błędzie.

Grupa `pogoda-faza3-deploy-test` zapobiega jednoczesnej podmianie tego samego
kontenera przez deploy testowy z FAZY-3 i FAZY-4. Job nie używa Terraform,
workspace ani tokenu DigitalOcean.

## Krok 16. Uruchom zdalny deploy `dev` i `prod`

Macierz zdalnego deploya działa na runnerze `pogoda-state`, po udanym `apply`.
Dla każdego Environment wykonuje:

1. Sprawdzenie zgodności digestu obrazu i adresu VM z przebiegiem oraz środowiskiem.
2. Odczyt wygenerowanego prywatnego klucza SSH ze stanu do tymczasowego
   pliku z prawami `0600`.
3. Połączenie SSH z VM i oczekiwanie na zakończenie cloud-init.
4. Logowanie Docker Hub na VM w osobnym tymczasowym katalogu konfiguracji.
5. Pobranie obrazu po digescie; błąd pobierania pozostawia obecny kontener.
6. Usunięcie tylko poprzedniego `pogoda_api_faza4_dev` lub `pogoda_api_faza4_prod`.
7. Uruchomienie nowego z `--restart unless-stopped`, mapowaniem `80:8080`
   oraz kluczem pogody przekazanym wyłącznie przy uruchamianiu.
8. Sprawdzenie `/healthz` i stanu kontenera.
9. Wylogowanie Docker Hub i usunięcie katalogu z tokenem na VM, także przy błędzie.
10. Sprawdzenie publicznego WWW oraz prawdziwego zapytania API z runnera.
11. Zapisanie URL w podsumowaniu joba i przy GitHub Environment.
12. Usunięcie tymczasowego prywatnego klucza na runnerze.

Token Docker Hub i klucz OpenWeather przechodzą przez szyfrowane stdin SSH.
Te wartości oraz token DigitalOcean nie trafiają do stanu, planu, cloud-init,
obrazu ani cache. **Wyjątkiem jest para SSH wygenerowana w Terraform:** jej
prywatna część znajduje się w stanie i może znajdować się w planie, zgodnie
z krokiem 7. Żaden z tych sekretów nie jest argumentem polecenia SSH.

Przy pierwszym połączeniu pipeline zapamiętuje klucz hosta VM w trwałym
`known_hosts`. Następne połączenia sprawdzają jego zgodność. Alias zawiera
ID Dropleta, więc nowa VM otrzyma osobny wpis. Jest to zaufanie przy pierwszym
połączeniu, a nie wcześniejsze potwierdzenie fingerprintu innym kanałem.
Zmiany klucza tej samej VM pipeline nie akceptuje.
[Opcja `accept-new` w OpenSSH](https://man.openbsd.org/ssh_config#StrictHostKeyChecking).

HTTP jest protokołem tego ćwiczenia. Aplikacja nadal pozwala przygotować
HTTPS, ale domeny, certyfikaty i port TLS nie wchodzą w tę fazę.

## Krok 17. Uruchom pipeline i odczytaj URL

1. Otwórz **Actions → workflow FAZY-4 → Run workflow**.
2. Wybierz gałąź zgodną z `FAZA4_DEPLOY_BRANCH` i uruchom przebieg.
3. Sprawdź wspólne `build` i `test`, potem trzy publikacje.
4. Otwórz osobne joby `init`, `validate`, `plan`, `apply` dla `dev` i `prod`.
   Sprawdź workspace oraz runner każdego wariantu.
5. Sprawdź deploy testowy i dwa zdalne deploye.
6. Z podsumowań udanych deployów skopiuj adresy `http://<IP_VM>/`.

Dla `dev` i `prod` używaj IP właściwej VM, a nie runnera. Przykładowo dla
zwróconego `203.0.113.20` byłoby to `http://203.0.113.20/`, bez dopisywania
`:18094`, `:18095` lub `:8080`. Adres przykładowy zastąp rzeczywistym outputem.

Sam output `app_url` z Terraform wskazuje adres VM. Dopiero udany końcowy
test WWW i API potwierdza działającą aplikację pod tym adresem.

## Krok 18. Zweryfikuj wynik jako słuchacz

1. Wejdź w przeglądarce na URL `dev` i wpisz nazwę miasta.
2. Powtórz dla `prod` i dotychczasowego URL `test`.
3. Sprawdź API, zastępując przykładowy IP faktycznym:

   ```bash
   curl --fail http://203.0.113.20/healthz
   curl --fail --get --data-urlencode 'city=Warsaw' http://203.0.113.20/api/weather
   ```

4. W DigitalOcean sprawdź dwie VM z Twoim identyfikatorem i losowym sufiksem
   oraz firewall przypisany do każdej z nich.
5. Na runnerze sprawdź dwa pliki `terraform.tfstate`: w `workspaces/dev/`
   oraz `workspaces/prod/`.
6. Uruchom cały workflow ponownie bez zmiany kodu. Build powinien użyć cache,
   Terraform zachować VM i sufiksy nazw, a deploy podmienić właściwe kontenery.
7. Zmień `FAZA4_WEATHER_CITY` tylko w Environment `dev`, np. na `Gdansk`,
   i sprawdź, czy test tego środowiska używa nowej wartości.

## Krok 19. Zachowaj stan i rozpoznawaj błędy

| Objaw | Co sprawdzić |
| --- | --- |
| Job czeka na runnera | Czy dokładnie jeden dostępny runner ma etykietę `pogoda-state` |
| Brak znacznika katalogu stanu | Czy krok 3 wykonano jako konto usługi runnera i ustawiono poprawny pełny `FAZA4_STATE_ROOT` |
| Brak wcześniejszego stanu | Przywróć właściwy stan; nie obchodź kontroli nowym pustym katalogiem |
| `403` z DigitalOcean | Zakres, ważność i konto tokenu `DIGITALOCEAN_TOKEN` |
| Klucz SSH już istnieje | Właściwy zachowany stan; nie rejestruj ponownie kluczy po utracie stanu |
| SSH nie odpowiada | Publiczny adres runnera, CIDR `/32`, firewall, właściwy workspace i wygenerowany klucz ze stanu |
| Niezgodny klucz hosta SSH | Tożsamość VM; nie wyłączaj sprawdzania klucza hosta |
| Błąd cloud-init | Instalację pakietów i dostęp VM do repozytoriów Ubuntu |
| `docker pull` odrzucony | Prawo odczytu obrazu, login i token Docker Hub |
| `/healthz` działa, pogoda nie | Aktywność i limit prawdziwego klucza OpenWeather, miasto, dostęp VM do API |
| URL VM nie działa | Sukces publicznego testu po deployu, IP właściwej VM i regułę TCP80 |

Nie usuwaj stanu również po **częściowo nieudanym `apply`**. Część zasobów
mogła już powstać, a zapisany stan pozwala kontynuować. Znaczniki pomagają
wykryć utratę poprzedniego stanu, ale nie zastępują kopii zapasowej.

Wykonuj kopię katalogu repozytorium i słuchacza w `FAZA4_STATE_ROOT`, gdy
workflow nie pracuje. Zachowaj stany `dev` i `prod`, kopie `.backup`, znaczniki
oraz zapisane klucze hostów SSH. Backup przechowuj poza checkoutem i poza Git.
Nie usuwaj `runs/<run_id>` w trakcie przebiegu: zawiera konfigurację,
`TF_DATA_DIR` i plan potrzebny następnym jobom.

Jeśli zasoby istnieją, a stan zniknął, zatrzymaj uruchamianie pipeline
i odtwórz poprawny stan albo uzgodnij import z prowadzącym. Pusty stan
nie oznacza pustego konta DigitalOcean.

Po szkoleniu uzgodnij z prowadzącym usunięcie płatnych VM i ich zasobów
przez Terraform, osobno dla `dev` i `prod`, z zachowanymi stanami. Nie kasuj
stanu jako sposobu usuwania infrastruktury. Ten workflow nie wykonuje
samoczynnego `destroy`.

## Kryteria ukończenia

- Jeden obraz zbudowany z cache i rzeczywiście przetestowany.
- Ten sam obraz opublikowany dla `test`, `dev`, `prod`.
- `init`, `validate`, `plan`, `apply` jako osobne joby GitHub Actions.
- Osobne VM oraz lokalne stany Terraform dla `dev` i `prod`.
- `test` nadal na runnerze, z konfiguracją FAZY-3.
- WWW i rzeczywiste API sprawdzone przez pipeline oraz słuchacza.
- Podsumowania udanych deployów zawierają URL z publicznymi IP VM.
- Kolejny przebieg zachowuje infrastrukturę i podmienia kontenery.
- Tokeny i klucz pogody pozostają poza obrazem oraz Terraform. Stan i plany
  z wygenerowanymi kluczami SSH są chronione na runnerze; tymczasowe logowania
  Docker Hub i pliki kluczy SSH są sprzątane.

Przygotowanie materiału i lokalna walidacja nie są wykonaniem `apply` ani
dowodem działania na DigitalOcean. Realny wynik potwierdza udany przebieg
słuchacza i jego weryfikacja WWW/API pod zwróconymi adresami.
