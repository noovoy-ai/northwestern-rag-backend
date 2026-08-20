# Nirene AI Workspace & Enterprise RAG - Proje ve Mimari Rehberi (`README.md`)

Bu doküman, **Nirene AI Workspace & Enterprise RAG** projesinin mimarisini, sistemin çalışma akışını, kurumsal RBAC/ABAC yetkilendirme modelini, veritabanı şemasını, arayüzdeki her tuşun işlevini, hata loglarının yerlerini ve sistemi sıfırdan kurma adımlarını içermektedir.

---

## 🏛️ Mimari Evrim ve Geçiş Gerekçeleri (Before / After Rationale)

Proje, tek kullanıcılı ve statik prototipten çok kullanıcılı, kurumsal güvenlik standartlarına sahip kurumsal bir mimariye dönüştürülmüştür:

| Bileşen | Önceki Durum (Legacy) | Yeni Mimari (Enterprise) | Geçiş / Tercih Gerekçesi |
| :--- | :--- | :--- | :--- |
| **Vektör Veritabanı** | `ChromaDB` (SQLite3 dosya tabanlı) | **PostgreSQL 15+ & `pgvector`** (HNSW Cosine İndeksi) | ChromaDB dosya kilitlenme sorunları yaratıyordu ve satır düzeyinde güvenlik (RLS) desteği yoktu. PostgreSQL pgvector ile veritabanı seviyesinde veri güvenliği ve eşzamanlı çoklu kullanıcı sağlandı. |
| **Yetkilendirme & Güvenlik** | Sabit Python sözlüğü (`USERS_DB`) + Temel JWT | **Supabase GoTrue (Auth) + PostgreSQL RLS & ABAC** | Önceden uygulama katmanında basit if-else ile yapılan yetkilendirme yerine, veritabanı seviyesinde `department` ve `min_clearance_level` filtreli RLS politikaları ile Sıfır Bağlam Sızıntısı (Zero Leakage) garanti altına alındı. |
| **Veri Yükleme (Ingestion)** | Statik `ingest.py` + `handbook_vectordb_ready.md` | **Dinamik PDF Ingestion API (`PyMuPDF` + `pdfplumber`)** | Sabit dosya bağımlılığı kaldırıldı; SHA-256 hash çakışma kontrolü, tablo korumalı Markdown dönüşümü, versiyonlama ve soft-delete destekli dinamik API'ye geçildi. |
| **Denetim & Loglama** | Yok (Sadece konsol logları) | **`audit_logs` ve `user_profiles` Metrik Sistemi** | Hangi kullanıcının hangi soruyu sorduğu, harcanan token'lar, kullanılan chunk ID'leri ve yanıt süreleri asenkron olarak kaydedilerek tam denetim izi sağlandı. |
| **Bilgi Kürasyonu** | Manuel müdahale | **Kurumsal Bilgi Havuzu (`Knowledge Flywheel`)** | Kullanıcı geri bildirimleri (`+1/-1`) ve admin onayıyla (`knowledge_staging`) model yanıtlarının kurumsal hafızaya otomatik eklenmesi sağlandı. |
| **LLM & Embedding Köprüsü** | Konteyner içi LangChain Community | **Host Seviyesinde Doğrudan Ollama API Köprüsü (`httpx`)** | Mac Mini M2 Metal GPU hızlandırmasını kaybetmemek için konteynerden host Ollama'ya (`host.docker.internal:11434`) bağlanan hafif, asenkron istemciye geçildi. |

---

## 📌 İÇİNDEKİLER
1. [Arayüz ve API Erişim Linkleri (Gidilen Her Adres)](#1-arayüz-ve-api-erişim-linkleri-gidilen-her-adres)
2. [Sohbet ve Dokümantasyon Arayüzündeki Her Tuşun İşlevi](#2-sohbet-ve-dokümantasyon-arayüzündeki-her-tuşun-işlevi)
3. [Veritabanı Mimarisi, RLS Güvenliği ve Vektör Arama](#3-veritabanı-mimarisi-rls-güvenliği-ve-vektör-arama)
4. [Hata Kodları ve Logları Nerede Bulabiliriz?](#4-hata-kodları-ve-logları-nerede-bulabiliriz)
5. [Sistemi Sıfırdan Tekrar Kurma ve Çalıştırma Rehberi](#5-sistemi-sıfırdan-tekrar-kurma-ve-çalıştırma-rehberi)
6. [Sistemin İşleyiş Mantığı ve Mimari Şemalar (Grafikler)](#6-sistemin-işleyiş-mantığı-ve-mimari-şemalar-grafikler)
7. [Giriş Bilgileri, Rol Matrisi ve Güvenlik Mimarisi](#7-giriş-bilgileri-rol-matrisi-ve-güvenlik-mimarisi)
8. [Ekip Çalışması, Git İş Akışı ve GitHub CI/CD Otomasyonu](#8-ekip-çalışması-git-iş-akışı-ve-github-cicd-otomasyonu)
9. [Yapay Zeka Ajanları Yönetimi ve AGENTS.md Rehberi](#9-yapay-zeka-ajanları-yönetimi-ve-agentsmd-rehberi)

---

## 1. Arayüz ve API Erişim Linkleri (Gidilen Her Adres)

Soru-Cevap servisi ve yönetim arayüzü yerel ve tünel ortamında şu portlar üzerinden çalışmaktadır:

### 🔗 Doğrudan Tıklanabilir Linkler ve İşlevleri:

1. 💬 **[Görsel Yapay Zeka Sohbet & Yönetim Arayüzü](http://localhost:8005/)** (`http://localhost:8005/`)
   - **Ne İşe Yarar?** Kullanıcıların giriş yapıp personel el kitabıyla ilgili soru sorabildiği, Admin'lerin PDF yükleyip onay bekleyen kürasyonları yönettiği modern web arayüzüdür.
2. 🌐 **[Yerel Swagger UI Test Arayüzü](http://localhost:8005/docs)** (`http://localhost:8005/docs`)
   - **Ne İşe Yarar?** FastAPI'nin otomatik oluşturduğu OpenAPI 3.1 test ekranıdır. `/api/auth/*`, `/api/documents/*`, `/api/chat/*` ve `/api/curation/*` endpoint'lerini test etmeyi sağlar.
3. 📑 **[Yerel ReDoc Dokümantasyonu](http://localhost:8005/redoc)** (`http://localhost:8005/redoc`)
   - **Ne İşe Yarar?** REST API servisinin şemalarını ve HTTP yanıt kodlarını standart bir dokümantasyon formatında sunar.
4. 💚 **[Servis Sağlık Kontrolü (Healthcheck)](http://localhost:8005/health)** (`http://localhost:8005/health`)
   - **Ne İşe Yarar?** Docker ve sistem izleme için backend servisinin ve veritabanı bağlantısının durumunu kontrol eder (`{"status": "healthy"}`).
5. 🔐 **[GoTrue Auth Servisi](http://localhost:9999/)** (`http://localhost:9999/`)
   - **Ne İşe Yarar?** Supabase GoTrue kimlik doğrulama, kullanıcı oluşturma ve JWT token dağıtım mikroservisidir.
6. 🗄️ **[PostgreSQL & pgvector Veritabanı](http://localhost:5432/)** (`localhost:5432`)
   - **Ne İşe Yarar?** RLS politikaları, vektör indeksleri (HNSW) ve denetim loglarını barındıran ana ilişkisel veritabanıdır.

---

## 2. Sohbet ve Dokümantasyon Arayüzündeki Her Tuşun ve Bileşenin İşlevi

### 💬 Web Sohbet ve Yönetim Arayüzü (`index.html`) Tuşları & Rozetleri:

- **🏷️ Kaynak Atıf Rozeti (`GENEL · Lv10 (%94 Eşleşme)`):**
  - **Departman ve Seviye (`GENEL · Lv10`, `FINANS · Lv50` vb.):** Cevabın üretilmesinde kullanılan kaynak bilginin hangi departmana ait olduğunu ve bu bilgiye erişmek için kullanıcının sahip olması gereken asgari yetki/güvenlik derecesini (*Clearance Level*) gösterir.
  - **Yüzde İfadesi (`%94 Eşleşme`, `%88 Eşleşme`):** Vektör veritabanındaki (*pgvector*) **Kosinüs Benzerlik Skorudur** (*Vector Cosine Similarity*). Sorulan cümlenin semantik/anlamsal vektörü ile ilgili politika metninin anlamsal örtüşme oranını gösterir (%90+ çok yüksek kesinlik).
- **👍 / 👎 Geri Bildirim Butonları (Faydalı / Faydasız):**
  - **1. Denetim İzi (Audit Log):** Kullanıcı bir cevabı beğendiğinde veya yetersiz bulduğunda, `audit_logs` tablosundaki ilgili kayda `user_feedback: 1` veya `-1` olarak işlenir.
  - **2. Kurumsal Bilgi Havuzu ve Kürasyon (`Knowledge Flywheel`):** Geri bildirim alan soru-cevaplar `knowledge_staging` tablosuna aktarılır. Departman yöneticileri veya Super Admin **Curation Pool** ekranından bu soru-cevapları inceleyip onaylayarak (*Approve*) kalıcı vektör belleğine dahil edebilir. Böylece sistem kurumsal hafızasını insan onayıyla sürekli zenginleştirir.
- **⚡ Fast Role Fill (Tek Tıkla Rol Doldurucu):**
  - Login ekranında Super Admin, İK Admin, Hukuk Admin, Finans Admin ve Genel Personel hesapları arasında tek tıkla geçiş yapmayı sağlar.
- **✨ Dinamik Soru Öneri Çipleri (Smart Chips):**
  - Giriş yapan kullanıcının departmanına ve yetki seviyesine göre ana ekrandaki öneri sorularını dinamik olarak değiştirir (Örn: Finans Admin için bütçe onayları, Hukuk Admin için NDA/Dava limitleri, Personel için izin hakları).
- **📤 Upload PDF (Doküman Yükle):**
  - *Kim Görebilir?* Sadece `super_admin` rolüne sahip kullanıcılar.
  - *Ne Yapar?* Departman ve minimum güvenlik seviyesi belirterek sisteme yeni PDF yükler, SHA-256 özetini çıkarır, tabloları Markdown formatına dönüştürür ve parçaları vektörleştirir.
- **✅ Curation Pool (Kürasyon Onay Havuzu):**
  - *Kim Görebilir?* Departman Adminleri ve Super Admin.
  - *Ne Yapar?* Kullanıcılar tarafından oylanan soru-cevap çiftlerini inceler, onaylandığında kalıcı doküman parçası olarak vektör veritabanına ekler.
- **🚪 Sign Out (Çıkış Yap) Butonu:**
  - *Ne Yapar?* Tarayıcının `localStorage` alanındaki JWT token ve rol bilgilerini temizler, tam ekran login kapısına döner.
- **➔ Send (Gönder) Butonu / Enter:**
  - *Ne Yapar?* Soruyu asenkron olarak `/api/chat/query` endpoint'ine iletir ve Server-Sent Events (SSE) ile yanıtı kelime kelime ekrana basar.

---

## 3. Veritabanı Mimarisi, RLS Güvenliği ve Vektör Arama

Sistem, veritabanı olarak **Supabase PostgreSQL 15+ ve pgvector** eklentisini kullanır:

### 3.1 Tablo Yapısı ve Şema (`db/schema.sql`):
1. `documents`: Başlık, SHA-256 hash, departman (`hukuk`, `finans`, `ik`, `genel`), `min_clearance_level`, versiyon ve aktiflik durumunu tutar.
2. `document_chunks`: Parçalanmış metinleri, metadata etiketlerini ve 768 boyutlu `vector(768)` embedding dizilerini HNSW Cosine indeksiyle (`vector_cosine_ops`) barındırır.
3. `user_profiles`: Kullanıcının departmanı, güvenlik seviyesi, toplam sorgu sayısı ve aktivite/güven skorlarını takip eder.
4. `audit_logs`: Kullanıcının sorgusu, kullanılan chunk ID'leri, LLM çıktısı, yürütme süresi (ms) ve harcanan token'ları arşivler.
5. `knowledge_staging`: Onay bekleyen kullanıcı geri bildirimlerini (`pending`, `approved`, `rejected`) tutar.

### 3.2 Satır Düzeyinde Güvenlik (Row-Level Security - RLS):
Tüm tablolarda `FORCE ROW LEVEL SECURITY` aktiftir. Bir kullanıcı sorgu attığında, kullanıcının JWT claims içeriğindeki `department` ve `clearance_level` değerleri veritabanı oturumuna `SET LOCAL ROLE authenticated;` ve `SET LOCAL request.jwt.claims` ile enjekte edilir. `SECURITY INVOKER` yetkisine sahip `match_documents` fonksiyonu yalnızca kullanıcının görmeye yetkili olduğu chunk'ları vektör benzerliğine göre sıralar.

---

## 4. Eklenen Departman Mockup Politikaları ve Demo Veri Seti

Sistemde ABAC / RLS izolasyonunun ve rol yetkilerinin sunumu için 4 departmana özel politika dokümanı indekslenmiştir (`seed_mockup_data.py`):

| Departman | Güvenlik Seviyesi | Doküman Adı | Kapsadığı Önemli Bilgiler |
| :--- | :--- | :--- | :--- |
| **Finans** | Level 50 (Gizli) | *2026 Finansal Harcama Limitleri ve Onay Matrisi* | 0-50k TL Birim Müdürü, 50k-250k TL VP/Direktör, 250k-1M TL CFO+CEO, **1M+ TL Yönetim Kurulu Kararı**, Yurt Dışı Harcırah (250 USD/gün), Şirket Kredi Kartı (5 iş günü masraf girişi) |
| **Hukuk** | Level 50 (Gizli) | *2026 Hukuk Müşavirliği Sözleşme, NDA ve Dava Yönetimi* | **Standart NDA gizlilik süresi 5 yıl**, Fikri mülkiyet şirkete ait, 200k+ TL sözleşme feshi Hukuk Müşaviri onayı, **500k+ TL dava açma Yönetim Kurulu Hukuk ve Risk Komitesi onayı** |
| **İK** | Level 50 (Gizli) | *2026 İK Yönetici Performans Primi ve Kariyer Skalası* | **Yönetici Üstün Başarı Primi %35 (yıllık brüt)**, Hedef Üstü %20, Seviye 3 Etik İhlal ve Gizli Soruşturma süresi 15 iş günü |
| **Genel** | Level 10 (Tüm Personel) | *2026 Genel Personel Çalışma Rehberi ve Sosyal Haklar* | **Haftada 2 gün uzaktan çalışma (Pazartesi/Cuma ofis önerisi)**, 1-5 yıl kıdem 14 gün, 5+ yıl 20 gün izin, En fazla 5 gün devir, **3 gün evlilik izni, 5 gün babalık izni**, Ticket Restaurant yükleme |

---

## 5. Hata Kodları ve Logları Nerede Bulabiliriz?

### 📜 Docker Konteynır Logları:
```bash
# Backend servis logları (FastAPI):
docker logs -f local-rag-backend

# Veritabanı ve SQL sorgu logları:
docker logs -f local-rag-db

# GoTrue kimlik doğrulama logları:
docker logs -f local-rag-auth

# Host Ollama model servis logları:
ollama logs

# 24/7 Tünel ve Çökme İzleme Servis Logları:
tail -f /Users/mini/agent_1/tunnel_watcher.log
```

### 🔢 HTTP Yanıt Hata Kodları:
- **`200 OK`:** İstek başarılı, sohbet cevabı veya token üretildi.
- **`400 Bad Request`:** Gönderilen soru metni boş veya dosya formatı geçersiz.
- **`401 Unauthorized`:** GoTrue JWT Token geçersiz veya süresi dolmuş.
- **`403 Forbidden`:** Kullanıcının departman veya güvenlik yetkisini aşan işlem talebi.
- **`409 Conflict`:** Aynı SHA-256 hash değerine sahip doküman zaten aktif olarak mevcut.
- **`500 Internal Server Error`:** Ollama bağlantı hatası veya veritabanı transaction hatası.

---

## 6. Sistemi Sıfırdan Tekrar Kurma ve Çalıştırma Rehberi

### ⚙️ Adım 1: Gereksinimleri Kontrol Edin
- macOS (Apple Silicon M2 / Metal GPU destekli)
- Docker Desktop
- Python 3.11+
- Ollama (`/opt/homebrew/bin/ollama`)

### 🧠 Adım 2: Ollama Modellerini Hazırlayın
```bash
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

### 🐳 Adım 3: Docker Konteynırlarını Başlatın
```bash
cd /Users/mini/agent_1
docker compose up -d --build
```

### 📄 Adım 4: Mockup Demo Verilerini İndeksleyin
```bash
docker exec -i local-rag-backend python3 - < seed_mockup_data.py
```
Servisler `http://localhost:8005/` adresinde çalışacaktır.

---

## 7. Sistemin İşleyiş Mantığı ve Mimari Şemalar (Grafikler)

### 📊 RBAC/ABAC RAG Akış Şeması

```mermaid
sequenceDiagram
    autonumber
    actor User as Kullanıcı / Web UI
    participant Gateway as FastAPI Backend
    participant Auth as GoTrue Auth (JWT)
    participant DB as PostgreSQL (RLS + pgvector)
    participant Ollama as Host Ollama (Qwen 2.5)

    User->>Gateway: POST /api/chat/query {"question": "..."}
    Gateway->>Auth: JWT Token Doğrula (Department & Clearance Level)
    Gateway->>Ollama: Soru Embedding'ini Al (nomic-embed-text)
    Ollama-->>Gateway: 768-Dim Vektör
    Gateway->>DB: SET LOCAL ROLE authenticated; SET LOCAL request.jwt.claims & match_documents()
    Note over DB: RLS Devrede: Yalnızca yetkili chunk'lar taranır
    DB-->>Gateway: Filtrelenmiş En Alakalı Parçalar (k=5)
    Gateway->>Ollama: Strict Prompt + Context + Soru (Streaming)
    Ollama-->>Gateway: Token Akışı (SSE)
    Gateway-->>User: Streaming Yanıt + Citation Badges
    Gateway-)DB: Audit Log & Kullanıcı Metrik Kaydı (Asenkron)
```

---

## 8. Giriş Bilgileri, Rol Matrisi ve Güvenlik Mimarisi

| Kullanıcı Adı | Şifre | Rol | Departman | Clearance | Yetki Kapsamı |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`admin`** | `admin*123!` | `super_admin` | `genel` | **100** | Tüm departman dokümanlarını görme, PDF yükleme, silme ve tam kürasyon onayı. |
| **`finans_admin`** | `finans*2026!` | `admin-finans` | `finans` | **50** | Finans harcama limitleri ve genel dokümanları görme; İK/Hukuk kilitlidir. |
| **`hukuk_admin`** | `hukuk*2026!` | `admin-hukuk` | `hukuk` | **50** | Hukuk NDA/dava politikaları ve genel dokümanları görme; Finans/İK kilitlidir. |
| **`ik_admin`** | `ik*2026!` | `admin-ik` | `ik` | **50** | İK yönetici primleri ve genel dokümanları görme; Finans/Hukuk kilitlidir. |
| **`staff`** | `nu2026pass` | `user-genel` | `genel` | **10** | Yalnızca genel personel çalışma saatleri ve izin politikalarını görme. |

---

## 9. Ekip Çalışması, Git İş Akışı ve GitHub CI/CD Otomasyonu

- **GitHub Repository:** [https://github.com/noovoy-ai/northwestern-rag-backend](https://github.com/noovoy-ai/northwestern-rag-backend)
- **Canlı / Dağıtım Branch:** `main` (Mac Mini 2 üzerinde çalışan dal)

Tüm geliştirmeler `main` dalına push edildiğinde GitHub Actions self-hosted runner otomatik olarak:
1. Docker yapılandırmasını güvenli şekilde hazırlar,
2. Konteynerleri yeniden derler ve ayağa kaldırır,
3. Healthcheck denetimini başarıyla tamamlar.

---

## 10. Dış Erişim Tüneli ve Çökme / Sağlık İzleme Servisi (`tunnel_watcher.py`)

- **Dış Erişim (Cloudflare Tunnel):** Cloudflare tüneli otomatik olarak `http://localhost:8005` portunu internete güvenli HTTPS bağlantısıyla açar (`https://*.trycloudflare.com`).
- **Otomatik E-Posta Bildirimi:** Tünel adresi her yenilendiğinde Apple Mail üzerinden `yunusemrec103@gmail.com` adresine canlı erişim linki iletilir.
- **Sağlık & Çökme İzleme:** macOS LaunchAgent (`com.nirene.tunnel-watcher.plist`) arka planda 7/24 çalışır ve her 10 saniyede bir `http://localhost:8005/health` adresini denetler. Arka arkaya 3 başarısızlık tespit edilirse yöneticiye anında acil durum çökme e-postası (`🚨 [ACİL UYARI] Nirene AI Sistemi Çöktü`) gönderilir; sistem toparlandığında ise kurtarma bildirimi (`✅ [SİSTEM KURTARILDI]`) iletilir.
