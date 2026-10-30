# UPS pilotu — kullanım ve iş kuralları

30 Ekim 2026

## Yapılan çalışma

İsmail’le bugün üzerinden geçilen süreçte kalan QR/barkod ve carrier entegrasyonu için, UPS bağlantısını denemeye yönelik küçük bir uygulama hazırlandı. Kutu ölçüleri, ağırlık ve adres bilgileri girilerek fiyat alınabiliyor; hizmet seçimi sonrasında label oluşturulup yazdırılabiliyor.

UPS test ortamında bağlantı, fiyat alma ve label oluşturma çalıştı. Serkan da ekran üzerinden fiyat alıp test label’ını yazdırdı. Müşteri hesabı boş bırakılan, Adams hesabının ödeyen olduğu senaryo denendi.

Uygulama şu anda bağımsız ve yerelde çalışıyor. İlk kapsam ABD içi paket gönderileri. Odoo bağlantısı ve müşteri hesabına gerçek faturalama sonraki aşamada değerlendirilecek; mevcut fiyat ve label’lar test çıktısı.

## İş kuralları ve Odoo bağlantısı

- Ölçüler kutunun dış ölçüleri, ağırlık ise ambalaj dahil brüt ağırlık olmalı. Ölçü ve ağırlık birimleri birlikte taşınmalı.
- Customer UPS account alanı opsiyonel olmalı. Boşsa taşıma bedeli Adams hesabına, doluysa müşteri hesabına yönlendirilmeli.
- Müşteri hesabının teslimat alıcısına mı, farklı bir müşteriye mi ait olduğu belirtilmeli. UPS’in istediği kayıtlı posta kodu ve gerektiğinde ülke bilgisi alınmalı.
- Müşteri hesabıyla işlem yapılamadığında hata gösterilmeli; ödeme otomatik olarak Adams hesabına aktarılmamalı.
- Odoo bağlantısında paket ve adres bilgileri ilgili `WH-AI/OUT` sevkiyatından alınmalı. Customer PO / Shipment PO bilgileri aynı ekranda görülebilmeli.
- Germany’de basılacak internal QR, US deposundaki ilgili `WH-AI/OUT` kaydını açmalı. Bu ekrandan fiyat ve label işlemine geçilebilmeli.
- Label ve tracking bilgileri sevkiyatla ilişkilendirilmeli. Yeniden yazdırmada mevcut label kullanılmalı; yeni shipment oluşmamalı.

Müşterinin indirimli fiyatını görüntülemek ile taşıma bedelini müşteri hesabına yönlendirmek ayrı yetkilere bağlı olabiliyor. Gerçek müşteri faturalaması ayrıca doğrulanmalı. Pilotun tekrar işlem kontrolü bellekte tutuluyor; Odoo bağlantısında bu kaydın kalıcı olması gerekiyor.

## Paketin kullanımı

Uygulamanın backend’i **Python**, ekranı **HTML/CSS ve JavaScript** ile yazıldı. Python 3.10+ ve güncel bir tarayıcı yeterli; ek Python paketi, Node.js, npm veya veritabanı kurulumu gerekmiyor. `server.py` uygulamayı başlatıyor, `ups_client.py` UPS bağlantısını yönetiyor; ekran dosyaları `index.html` ve `app.js`.

ZIP açıldıktan sonra `README.md` ile `shipping_pilot` klasörünün bulunduğu paket kökünde aşağıdaki komutla uygulama başlatılabiliyor:

```sh
python3 shipping_pilot/server.py --port 8765
```

Ekran adresi: **http://127.0.0.1:8765/**

Windows’ta aynı komut `py -3 shipping_pilot/server.py --port 8765` şeklinde çalışıyor. Terminal açık kaldığı sürece uygulama çalışır; Ctrl+C ile durur. HTML dosyasını doğrudan açmak yerine bu adres kullanılıyor. Adres, uygulamanın çalıştığı bilgisayarda erişilebilir.

Credentials olmadan form açılıyor. UPS bağlantısı için paketteki `shipping_pilot/UPS_CONFIG.example.json` örneğinden, paket dışında bir `config.json` hazırlanıyor. Dosyada Client ID, Client Secret, Adams UPS hesap numarası ve isteğe bağlı çıkış adresi bulunuyor. Credentials pakete dahil değil; ayrıca paylaşılacak. Örnek JSON’daki değerler gerçek bilgilerle değiştirilir; geçici access token girilmesine gerek yok, uygulama token’ı kendisi alır. Aşağıdaki dosya yolları, oluşturulan gerçek `config.json` yoluyla değiştirilir.

macOS/Linux üzerinde örnek çalıştırma:

```sh
export UPS_CONFIG_PATH="/ozel/klasor/config.json"
python3 shipping_pilot/server.py --port 8765
```

Windows PowerShell karşılığı:

```powershell
$env:UPS_CONFIG_PATH = "C:\ozel\config.json"
py -3 shipping_pilot/server.py --port 8765
```

Bağlantı kontrolü ekrandaki **Test UPS connection** düğmesiyle yapılabiliyor. Python sertifika hatası verirse güvenilir CA dosyası `UPS_CA_BUNDLE` ile belirtilebiliyor; macOS’ta bu pilotta `export UPS_CA_BUNDLE=/etc/ssl/cert.pem` kullanıldı. Diğer ortamlarda uygun CA dosyası değişebilir.

Ekrandaki kullanım sırası: **paket ve adres bilgileri → varsa müşteri hesabı → Get UPS test rates → hizmet seçimi → Create test label → Print test label**. Label için telefon alanları da dolduruluyor.

Kurulum ayrıntıları ve test komutları [README.md](README.md) dosyasında bulunuyor.
