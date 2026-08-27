# İzmir Etkinlik Botu

İzmir'deki etkinlikleri **birden fazla kaynaktan** toplayan, aynı etkinliğin farklı
sitelerdeki farklı yazımlarını **tek kayda indiren** ve sonucu **Telegram** üzerinden
tarih ve fiyat kırılımıyla sunan bot.

```
Sezen Aksu                                    (Bubilet)
Sezen Aksu Konseri                            (Biletinial)     ─┐
Sezen Aksu - Kültürpark Açıkhava Tiyatrosu    (OGGUSTO)         ├─→  tek etkinlik
Efsane Sanatçı Sezen Aksu İzmir'de!           (İzmirMag)        │    5 kaynakta doğrulanmış
SEZEN AKSU | İZMİR                            (Biletix)        ─┘    450–1.200 ₺
```

---

## Ne yapar?

- **8 kaynağı** tarar (3 tanesi daha hazır ama kapalı — aşağıya bakın)
- Türkçe tarih biçimlerini ayrıştırır: `12 Eylül 2026`, `12 - 15 Eylül`, `12.09.2026 21:00`,
  `Cumartesi 21.00`, `Bugün`, `28 Aralık - 5 Ocak` (yıl geçişi dahil)
- **Ücretsiz / ücretli** ayrımı yapar: `Ücretsiz`, `Giriş Serbest`, `450₺'den başlayan`,
  `150 TL - 400 TL`
- **Tekilleştirir**: aynı etkinliğin farklı yazımlarını birleştirir, farklı etkinlikleri
  ayrı tutar (`Hamlet` ≠ `Hamlet Makinesi`)
- Tarih kovalarına ayırır: **bugün / yarın / hafta sonu / bu hafta / gelecek hafta / bu ay / ileride**
- Telegram'da komut + buton arayüzü, **günlük sabah bülteni**, **yeni etkinlik bildirimi**
- Kaynak sağlığını izler: bir site tasarımını değiştirip scraper sessizce bozulursa
  size haber verir

---

## Hızlı başlangıç (yerel)

```bash
git clone https://github.com/OrbaySkrcl/izmir-etkinlik.git
cd izmir-etkinlik

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env      # TELEGRAM_BOT_TOKEN'ı doldurun
```

### Botsuz deneme

Telegram olmadan da çalışır — önce kaynakların ne döndürdüğüne bakın:

```bash
izmir-etkinlik sources                    # tanımlı kaynakları listele
izmir-etkinlik doctor --source bubilet    # tek kaynağı teşhis et
izmir-etkinlik scrape --no-store -n 40    # hepsini tara, tabloyu ekrana bas
izmir-etkinlik scrape                     # tara ve veritabanına yaz
izmir-etkinlik list --bucket bu_hafta     # kayıtlıları listele
izmir-etkinlik list --free                # sadece ücretsizler
izmir-etkinlik stats                      # kaynak sağlığı
```

### Botu çalıştırma

```bash
izmir-etkinlik serve
```

---

## Telegram kurulumu

1. Telegram'da [@BotFather](https://t.me/BotFather)'a `/newbot` yazın, isim ve kullanıcı
   adı verin. Size `123456789:AAE...` biçiminde bir token verecek.
2. Token'ı `.env` dosyasına yazın: `TELEGRAM_BOT_TOKEN=...`
3. Kendi Telegram kullanıcı id'nizi [@userinfobot](https://t.me/userinfobot)'tan öğrenip
   `TELEGRAM_ADMIN_IDS`'e ekleyin. Yönetici olarak `/tara` komutunu kullanabilir ve
   kaynak arıza uyarılarını alırsınız.
4. `izmir-etkinlik serve` ile başlatın, botunuza `/start` yazın.

### Komutlar

| Komut | Ne yapar |
|---|---|
| `/bugun` `/yarin` `/haftasonu` | Yakın tarihler |
| `/hafta` `/gelecekhafta` `/ay` `/ileride` | Daha geniş aralık |
| `/ucretsiz` | Sadece ücretsiz etkinlikler |
| `/ara sezen aksu` | İsim ve mekana göre arama (Türkçe karakter duyarsız) |
| `/kategori` | Konser, tiyatro, sergi, çocuk… |
| `/abone` | Günlük sabah bültenini aç/kapat |
| `/ayarlar` | Bildirim tercihleri (bülten, yeni etkinlik, sadece ücretsiz) |
| `/durum` | Kaç etkinlik var, kaynaklar sağlıklı mı |
| `/tara` | *(yönetici)* Elle tarama başlat |

Komut yazmadan düz metin gönderirseniz arama olarak yorumlanır.

---

## Railway'e dağıtım

Railway hesabınız hazırsa:

1. **Yeni proje** → *Deploy from GitHub repo* → bu depoyu seçin.
   Railway `railway.json` + `Dockerfile`'ı otomatik kullanır.

2. **Postgres ekleyin** (önerilir): proje ekranında *New → Database → PostgreSQL*.
   Railway `DATABASE_URL`'i otomatik enjekte eder; bot bunu async sürücüye kendi çevirir.

   > Postgres eklemezseniz SQLite kullanılır. O durumda `/app/data` yoluna bir
   > **Volume** bağlayın, yoksa her yeniden dağıtımda veritabanı sıfırlanır.

3. **Değişkenleri girin** (*Variables* sekmesi):

   ```
   TELEGRAM_BOT_TOKEN=123456789:AAE...
   TELEGRAM_ADMIN_IDS=123456789
   SCRAPE_INTERVAL_MINUTES=180
   DIGEST_HOUR=9
   ```

4. **Dağıtın.** Loglarda `bot_ready` satırını görünce Telegram'dan `/start` yazın.

### Polling mi webhook mu?

Varsayılan **long polling** — ek yapılandırma istemez ve Railway'de sorunsuz çalışır.
Webhook isterseniz servise bir domain atayın (*Settings → Networking → Generate Domain*)
ve şu değişkenleri ekleyin:

```
WEBHOOK_URL=https://sizin-servisiniz.up.railway.app
WEBHOOK_SECRET=rastgele-uzun-bir-dize
PORT=8080
```

Webhook modu tek replika ile çalışmalıdır (`numReplicas: 1`).

---

## Kaynaklar

| Anahtar | Kaynak | Öncelik | Not |
|---|---|---|---|
| `kultursanat` | [İzmir Kültür Sanat (İBB)](https://kultursanat.izmir.bel.tr/) | 90 | Resmî takvim; ücretsiz etkinlik yoğun |
| `biletix` | Biletix İzmir | 75 | *(ek kaynak)* En geniş kapsam |
| `bubilet` | [Bubilet İzmir](https://www.bubilet.com.tr/izmir) | 70 | Konser/tiyatro/stand-up |
| `biletinial` | [Biletinial İzmir](https://biletinial.com/tr-tr/sehrineozel/izmir) | 70 | Tiyatro ağırlıklı |
| `biletimgo` | [BiletimGo İzmir](https://www.biletimgo.com/sehir-etkinlikleri/izmir) | 60 | |
| `mobilet` | Mobilet İzmir | 55 | *(ek kaynak)* |
| `oggusto` | [OGGUSTO Etkinlik Rehberi](https://www.oggusto.com/etkinlik-rehberi/izmir) | 40 | Editoryal; başlıklar uzun |
| `izmirmag` | [İzmirMag](https://izmirmag.net/) | 30 | Haber sitesi; en gürültülü |

Kapalı gelen (açmadan önce `doctor` ile doğrulayın): `aassm`, `devlettiyatrolari`, `izmirdob`.

**Öncelik** ne işe yarar: birden fazla kaynak aynı etkinliği verdiğinde başlık, mekan ve
görsel yüksek öncelikli kaynaktan alınır. Fiyat aralığı ise tüm kaynakların birleşimidir.

### Yeni kaynak eklemek

`config/sources.yaml`'a bir blok ekleyin — **kod yazmanıza gerek yok**:

```yaml
  - key: yeni_kaynak
    name: "Yeni Kaynak"
    base_url: "https://ornek.com/izmir"
    listing_urls:
      - "https://ornek.com/izmir/etkinlikler"
    strategies: [jsonld, nextdata, selectors, heuristic]
    priority: 50
    free_by_default: false     # belediye/müze kaynaklarında true yapın
    city_filter: false         # site İzmir'e özel değilse true yapın
    pagination: { param: "sayfa", start: 1, max_pages: 3 }
    selectors:
      item: ".etkinlik-karti"
      title: "h3, .baslik"
      url: "a@href"
      date: ".tarih, time@datetime"
      venue: ".mekan"
      price: ".fiyat"
```

---

## Nasıl çalışıyor?

### 1. Katmanlı çıkarım

Sabit CSS seçicilerine güvenmek kırılgandır: site tasarımını değiştirdiğinde scraper
sessizce boş döner. Bu yüzden her sayfada dört strateji denenir ve **en çok kayıt
üreteni** seçilir:

| Strateji | Nasıl çalışır | Neden |
|---|---|---|
| `jsonld` | `<script type="application/ld+json">` içindeki `schema.org/Event` | Makine-okur, SEO için yayınlanır, en güvenilir |
| `nextdata` | `<script id="__NEXT_DATA__">` içindeki sayfa verisi | HTML değişse de bu yapı kalır |
| `selectors` | `sources.yaml`'daki CSS seçicileri | Kod değiştirmeden düzeltilebilir |
| `heuristic` | Tekrar eden "kart" yapısını kendi bulur | Son çare; site tamamen yenilense bile bir şey döner |

Bir kaynak boş dönmeye başladığında hangi katmanın bozulduğunu görmek için:

```bash
izmir-etkinlik doctor --source bubilet --save-html
```

Çıktı her stratejinin kaç kayıt bulduğunu, kaçının tarihinin ayrıştırılabildiğini ve
örnek kayıtları gösterir. `--save-html` ile indirilen sayfayı diske yazıp yapısına
bakabilir, `sources.yaml`'daki seçicileri düzeltebilirsiniz.

> **Not:** Bu depodaki seçiciler yaygın kalıplara göre yazıldı ve canlı sitelere karşı
> doğrulanmadı; siteler HTML'lerini sık değiştirir. İlk kurulumda her kaynak için bir
> kez `doctor` çalıştırıp `sources.yaml`'ı kalibre etmeniz beklenir. JSON-LD ve
> `__NEXT_DATA__` katmanları çoğu bilet sitesinde seçici gerektirmeden çalışır.

### 2. Tekilleştirme

Asıl zor kısım. Sıra şöyle:

1. **Tarih kapısı** — tarih aralıkları (±1 gün toleransla) kesişmiyorsa karşılaştırma
   bile yapılmaz. Bu aynı zamanda *bloklama* görevi görür: 500 etkinlikte kaba kuvvet
   ~125.000 karşılaştırma yaparken, tarihe göre bloklama bunu birkaç yüze indirir.
2. **URL eşitliği** — iki kayıt aynı sayfaya işaret ediyorsa kesin aynı etkinliktir.
3. **Başlık normalizasyonu** — Türkçe'ye duyarlı küçük harf (`İ`→`i`, `I`→`ı`), aksan
   sadeleştirme, parantez/yıl temizliği ve gürültü sözcüklerinin atılması
   (`konseri`, `bileti`, `izmir`, `efsane`, `sanatçı`…).
4. **Benzerlik puanı** — `token_set_ratio` ile `token_sort_ratio`'nun ortalaması.
   Sadece `token_set` kullanmak `"hamlet"` ⊂ `"hamlet makinesi"` durumunda 100 döndürüp
   yanlış birleştirme yapardı; `token_sort` uzunluk farkını cezalandırarak bunu engeller.
5. **Mekan doğrulaması** — mekan benzerliği puanın %28'i. Mekanlar açıkça farklıysa
   (aynı oyun, aynı gece, iki ayrı sahne) başlık birebir aynı olsa bile birleştirilmez.
6. **Tek kelime koruması** — taraflardan birinin başlığı tek kelimeyse birebir eşitlik
   aranır. `"Hamlet"` çok genel; tek token eklemek yapıtın kimliğini değiştirir.
7. **Birleştirme** — en yüksek öncelikli ve en okunaklı başlığa sahip kayıt kanonik
   seçilir (`SEZEN AKSU | İZMİR` yerine `Sezen Aksu`); eksik alanlar diğer kayıtlardan
   tamamlanır, fiyat aralığı genişletilir, tarih aralığı en genişi alınır.

Eşik `DEDUP_THRESHOLD` ile ayarlanabilir (varsayılan `0.82`). Düşürürseniz daha çok
birleşir (yanlış birleştirme riski artar), yükseltirseniz daha az.

### 3. Veri temizliği

Kaynak sayfaları kirli veri üretir; bot bunları listeye almadan önce eler:

| Sorun | Örnek | Ne yapılır |
|---|---|---|
| Tarih elemanı başlık sanılır | `29 Ağustos 2026` | Başlık adayları sırayla denenir, tarihten ibaret olanlar atlanır; hepsi tarihse kayıt elenir |
| Tarih başlığa yapışıktır | `Konken Partisi ... Tiyatrosu 18 Eylül 2026` | Tarih ifadesi başlıktan ayıklanır (hem gösterim hem tekilleştirme için) |
| Mekan başlığın içindedir | `Sezen Aksu \| Kültürpark Açıkhava Tiyatrosu` | Ayraçtan sonraki parça mekan sözcüğü içeriyorsa mekana taşınır |
| Mekan seçicisi tutmaz | `<div>Konak Sahnesi · 400 TL</div>` | Kart elemanları gezilir, mekan sözcüğü içeren kısa parça alınır (fiyat/tarih içerenler elenir) |
| Fiyat seçicisi tutmaz | aynı kart | Kart metninde para birimine bitişik tutar aranır |

Gün adları yalnızca metinde gerçek bir tarih varsa silinir; `Pazar Yeri Festivali`
ve `Cuma Konserleri` gibi başlıklar bozulmaz.

### 4. Nazik tarama

- Host başına eşzamanlılık sınırı ve istekler arası bekleme (`HTTP_DELAY_SECONDS`)
- `robots.txt` kontrolü (`RESPECT_ROBOTS=true`, üretimde açık bırakın)
- Geçici hatalarda üstel geri çekilmeli yeniden deneme (2s → 4s → 8s)
- Geliştirirken aynı sayfayı tekrar indirmemek için disk önbelleği

---

## Yapı

```
src/izmir_events/
├── config.py            Ortam değişkenleri + sources.yaml şeması
├── models.py            RawEvent / Event / Category
├── pipeline.py          tara → tekilleştir → kaydet
├── render.py            Telegram mesaj biçimlendirme
├── cli.py               serve / scrape / doctor / list / stats
├── util/
│   ├── text.py          Türkçe normalizasyon (dedup'ın temeli)
│   ├── dates.py         Türkçe tarih ayrıştırma + tarih kovaları
│   └── money.py         Fiyat ve ücretsizlik tespiti
├── scrape/
│   ├── http.py          Nazik HTTP istemcisi
│   ├── extractors.py    jsonld / nextdata / selectors / heuristic
│   └── runner.py        Kaynak koşucusu + filtreler
├── dedup/
│   ├── similarity.py    İki etkinlik aynı mı?
│   └── cluster.py       Bloklama, union-find, birleştirme
├── store/               SQLAlchemy şema + sorgular
└── bot/                 Komutlar, butonlar, zamanlanmış işler
```

---

## Yapılandırma

Tüm ayarlar ortam değişkeni; tam liste için `.env.example`. Sık kullanılanlar:

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | **Zorunlu.** BotFather token'ı |
| `TELEGRAM_ADMIN_IDS` | boş | `/tara` yetkisi ve arıza uyarıları |
| `DATABASE_URL` | SQLite | Railway'de Postgres otomatik gelir |
| `SCRAPE_INTERVAL_MINUTES` | `180` | Tarama sıklığı (`0` = kapalı) |
| `DIGEST_HOUR` | `9` | Günlük bülten saati (Europe/Istanbul) |
| `DEDUP_THRESHOLD` | `0.82` | Birleştirme eşiği |
| `HTTP_DELAY_SECONDS` | `1.0` | Aynı siteye istekler arası bekleme |
| `RESPECT_ROBOTS` | `true` | robots.txt'e uy |
| `ONLY_SOURCES` | boş | Test için tek kaynak: `bubilet,biletinial` |

---

## Geliştirme

```bash
pytest -q                              # 241 test
pytest --cov=izmir_events              # kapsam raporu
ruff check src tests && ruff format src tests
mypy src/izmir_events
```

Testler ağ kullanmaz: HTTP `respx` ile taklit edilir, HTML örnekleri
`tests/fixtures/` altındadır. Referans gün sabittir (27 Ağustos 2026), böylece
tarih mantığı takvimden bağımsız doğrulanır.

---

## Sorun giderme

**Bot cevap vermiyor** → `TELEGRAM_BOT_TOKEN` doğru mu? Loglarda `bot_ready` var mı?
Aynı token ile başka bir yerde ikinci bir kopya çalışıyorsa polling çakışır.

**Etkinlik gelmiyor** → `izmir-etkinlik stats` ile kaynak sağlığına bakın, sonra
sorunlu kaynak için `izmir-etkinlik doctor --source <anahtar> --save-html` çalıştırın.

**Etkinlik adı yerine tarih görünüyor** (ör. "29 Ağustos 2026") → Bu artık
otomatik eleniyor. Hâlâ görüyorsanız veritabanında eski kayıtlar duruyordur:
`izmir-etkinlik scrape --no-cache` ile yeniden tarayın.

**Mekan (📍) veya fiyat görünmüyor** → Önce `izmir-etkinlik doctor --source
<anahtar> --save-html` çalıştırıp kartın HTML'ine bakın, sonra
`config/sources.yaml` içindeki `venue` / `price` seçicilerini düzeltin.
Seçici olmadan da kart metninden okumaya çalışılır, ama doğru seçici her zaman
daha isabetlidir.

**Aynı etkinlik iki kez görünüyor** → `DEDUP_THRESHOLD`'u kademeli düşürün (0.78 deneyin).
Başlıklar çok farklıysa `src/izmir_events/util/text.py` içindeki `NOISE_TOKENS`'a
ilgili gürültü sözcüğünü ekleyin.

**Farklı etkinlikler birleşiyor** → `DEDUP_THRESHOLD`'u yükseltin (0.88 deneyin).

**Railway'de veri kayboluyor** → SQLite kullanıyorsanız `/app/data` yoluna Volume
bağlayın veya Postgres ekleyin.

---

## Lisans

MIT
