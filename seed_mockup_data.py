#!/usr/bin/env python3
"""
Nirene AI Workspace - Mockup Data Seeder
Creates department-isolated and clearance-isolated policy documents in pgvector:
- finans (Level 50): Bütçe Harcama Limitleri ve Onay Matrisi
- hukuk (Level 50): Sözleşme, NDA ve Dava Yönetim Standartları
- ik (Level 50): Yönetici Performans Primi ve Kariyer Skalası
- ik (Level 10): Personel İzin ve Yan Haklar Politikası
- genel (Level 10): Genel Kurumsal Rehber, Çalışma Saatleri ve Sosyal Haklar
"""

import asyncio
import uuid
import hashlib
import json
import asyncpg
from app.config import settings
from app.services.ollama_client import OllamaClient

ADMIN_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

MOCKUP_DOCS = [
    {
        "title": "2026 Finansal Harcama Limitleri ve Yönetim Kurulu Onay Matrisi",
        "department": "finans",
        "min_clearance_level": 50,
        "chunks": [
            """### Finans Departmanı 2026 Harcama ve Satınalma Onay Yetki Matrisi

Nirene A.Ş. 2026 mali yılı harcama onay yetki kademeleri aşağıdaki gibi belirlenmiştir:
1. **0 - 50.000 TL Arası Harcamalar:** İlgili Birim Yöneticisi (Manager) tek imzasıyla onaylanabilir.
2. **50.001 - 250.000 TL Arası Harcamalar:** Finans Direktörü ve İlgili Bölüm Başkanı (VP) ortak onayı gereklidir.
3. **250.001 - 1.000.000 TL Arası Harcamalar:** CFO (Mali İşler Başkanı) ve Genel Müdür (CEO) müşterek imzası zorunludur.
4. **1.000.000 TL Üzeri Harcamalar:** Yönetim Kurulu (Board of Directors) onay kararı ve 2 Yönetim Kurulu Üyesinin ıslak/e-imzası şarttır.""",

            """### Şirket Kredi Kartı, Temsil Ağırlama ve Masraf Politikası

1. **Masraf Giriş Süresi:** Şirket kredi kartı harcamaları ve nakit masraf formları harcamanın yapıldığı tarihten itibaren en geç 5 iş günü içinde SAP Finans modülüne fatura aslı veya e-arşiv PDF'i ile yüklenmelidir.
2. **Temsil ve Ağırlama:** 1.000 TL üzeri tüm müşteri ağırlama yemeklerinde faturaya görüşülen kurum ve katılımcı isim listesi eklenmelidir.
3. **Yurt Dışı Harcırah ve Seyahat Avansları:**
   - Yurt dışı iş seyahatlerinde günlük azami harcırah konaklama hariç 150 USD (veya 130 EUR)'dur.
   - Seyahat avansları seyahat bitimini takip eden 7 iş günü içinde muhasebeleşip kapatılmalıdır. Kapatılmayan avanslar personelin bir sonraki ay maaşından mahsup edilir."""
        ]
    },
    {
        "title": "2026 Hukuk Müşavirliği Sözleşme, NDA ve Dava Yönetim Politikası",
        "department": "hukuk",
        "min_clearance_level": 50,
        "chunks": [
            """### Gizlilik Sözleşmeleri (NDA) ve Fikri Mülkiyet Standartları

1. **Standart NDA Zorunluluğu:** Tüm üçüncü taraf iş ortakları, tedarikçiler ve danışmanlarla iş görüşmelerine başlamadan önce Hukuk Müşavirliği onaylı "Nirene Çift Taraflı Gizlilik Sözleşmesi (Bilateral NDA)" imzalanmalıdır.
2. **Gizlilik Süresi:** Nirene standart NDA sözleşmelerinde gizlilik ve ticari sır saklama yükümlülüğü sözleşme ilişkisinin sona ermesinden itibaren asgari 5 (beş) yıl süreyle devam eder.
3. **Fikri ve Sınai Haklar:** Çalışanların veya sözleşmeli danışmanların şirket kaynakları ve mesaisi dahilinde ürettiği tüm yazılım kodları, yapay zeka modelleri, veri setleri ve algoritmalar 6769 sayılı Sınai Mülkiyet Kanunu uyarınca kayıtsız şartsız Nirene A.Ş.'ye aittir.""",

            """### Dava Açma, İcra Takibi ve Hukuki Uyuşmazlık Yetkileri

1. **Ticari Alacak Takipleri:** 500.000 TL'ye kadar olan vadesi geçmiş ticari alacaklar ve icra takipleri Baş Hukuk Müşaviri onayıyla başlatılır.
2. **Büyük Ölçekli Davalar:** 500.000 TL üzerindeki tüm alacak, tazminat ve tahkim davalarında Yönetim Kurulu Hukuk ve Risk Komitesi'nin yazılı onayı zorunludur.
3. **Sözleşme Fesihleri:** Yıllık tutarı 200.000 TL'yi aşan hizmet ve lisans sözleşmelerinin tek taraflı haklı feshinde Hukuk Departmanından yazılı uygunluk görüşü (legal clearance) alınmadan karşı tarafa ihtarname çekilemez."""
        ]
    },
    {
        "title": "2026 İK Yönetici Performans Primi ve Kariyer Skalası Prosedürü",
        "department": "ik",
        "min_clearance_level": 50,
        "chunks": [
            """### Yönetici Kadrosu (Level 50+) Yıllık Performans Primi Skalası

Nirene 2026 Yönetici Kadrosu Yıllık Başarı Primi oranları yıl sonu KPI gerçekleşme yüzdesine göre şu şekilde hesaplanır:
1. **Üstün Başarı Derecesi (%115 ve Üzeri Gerçekleşme):** Yönetici yıllık brüt maaşının %35'i (otuz beş) oranında net prim hak eder.
2. **Hedef Üstü Derece (%100 - %114 Gerçekleşme):** Yönetici yıllık brüt maaşının %20'si (yirmi) oranında prim hak eder.
3. **Beklenen Düzey (%85 - %99 Gerçekleşme):** Yönetici yıllık brüt maaşının %10'u (on) oranında prim hak eder.
4. **Geliştirilmesi Gereken Düzey (%85 Altı):** Prim ödenmez ve 6 aylık Performans Geliştirme Planı (PIP) uygulanır.""",

            """### Şirket İçi Etik İhlal ve Gizli Soruşturma Prosedürü

1. **Etik Bildirimler:** Çıkar çatışması, rüşvet veya mobbing gibi Seviye 3 ihbarlar doğrudan İK Direktörü ve Bağımsız Denetim Komitesine iletilir.
2. **Soruşturma Süresi:** İK Etik Kurulu bildirimi aldığı tarihten itibaren en geç 10 iş günü içinde gizli tahkikatı tamamlar ve raporu CEO'ya sunar. Soruşturma süresince bildirimde bulunan çalışanın kimliği tamamen gizli tutulur."""
        ]
    },
    {
        "title": "2026 Genel Personel Çalışma Rehberi, İzinler ve Sosyal Haklar",
        "department": "genel",
        "min_clearance_level": 10,
        "chunks": [
            """### Çalışma Saatleri, Hibrit Düzen ve Kıyafet Yönetmeliği

1. **Haftalık Mesai:** Haftalık çalışma süresi 40 saattir. Günlük mesai saatleri 09:00 - 18:00 arasındadır (1 saat öğle molası dahil).
2. **Hibrit Çalışma:** Tüm personel, birim yöneticisinin onayı doğrultusunda haftada azami 2 (iki) iş günü uzaktan (remote) çalışabilir. Pazartesi ve Cuma günleri şirket içi koordinasyon toplantıları sebebiyle ofiste bulunulması önerilir.
3. **Kıyafet Düzeni:** Şirketimizde "Smart Casual" (Akıllı Rahat) serbest kıyafet kuralı uygulanmaktadır.""",

            """### Yıllık Ücretli İzin ve Mazeret İzni Hakları

1. **Yıllık İzin Hak Edişleri:**
   - 1 yıldan 5 yıla kadar kıdemi olan personele: Yılda 14 iş günü ücretli izin.
   - 5 yıldan fazla kıdemi olan personele: Yılda 20 iş günü ücretli izin verilir.
   - Kullanılmayan yıllık izinlerden azami 5 iş günü bir sonraki takvim yılına devredilebilir.
2. **Mazeret ve Özel İzinler:**
   - **Evlilik İzni:** 3 iş günü ücretli izin.
   - **Babalık İzni:** 5 iş günü ücretli izin.
   - **Vefat İzni (1. Derece Yakın):** 3 iş günü ücretli izin.
   - **Yemek ve Ulaşım Desteği:** Her ayın 1'inde tüm çalışanların kartına Ticket Restaurant yemek bedeli ve kurumsal ulaşım kartı yüklenir."""
        ]
    }
]

async def seed_data():
    ollama = OllamaClient()
    
    print("Connecting to PostgreSQL database...")
    conn = await asyncpg.connect(settings.DATABASE_URL)
    
    try:
        print("Cleaning previous demo documents...")
        await conn.execute("DELETE FROM document_chunks WHERE department IN ('finans', 'hukuk', 'ik', 'genel');")
        await conn.execute("DELETE FROM documents WHERE department IN ('finans', 'hukuk', 'ik', 'genel');")
        
        for doc in MOCKUP_DOCS:
            doc_id = uuid.uuid4()
            title = doc["title"]
            dept = doc["department"]
            clearance = doc["min_clearance_level"]
            chunks = doc["chunks"]
            file_hash = hashlib.sha256(f"{title}_{dept}_{clearance}".encode()).hexdigest()
            
            print(f"\n📄 Ekleniyor: [{dept.upper()} · Lv{clearance}] {title}")
            await conn.execute("""
                INSERT INTO documents (id, title, file_hash, department, min_clearance_level, version, is_active, uploaded_by)
                VALUES ($1, $2, $3, $4, $5, 1, TRUE, $6)
            """, doc_id, title, file_hash, dept, clearance, ADMIN_USER_ID)
            
            for idx, chunk_text in enumerate(chunks):
                print(f"   ↳ Vektörleştiriliyor (Chunk {idx+1}/{len(chunks)})...")
                embedding = await ollama.get_embedding(chunk_text)
                emb_str = f"[{','.join(map(str, embedding))}]"
                
                await conn.execute("""
                    INSERT INTO document_chunks (
                        id, document_id, content, chunk_index, department, min_clearance_level, is_active, source_type, metadata, embedding
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, TRUE, 'pdf', $7, CAST($8 AS vector)
                    )
                """, uuid.uuid4(), doc_id, chunk_text.strip(), idx, dept, clearance,
                    json.dumps({"title": title, "section": f"Chunk {idx+1}"}),
                    emb_str
                )
        
        print("\n✅ Tüm mockup politika dokümanları başarıyla pgvector'e eklendi ve indekslendi!")
        
        count_docs = await conn.fetchval("SELECT COUNT(*) FROM documents WHERE is_active = TRUE;")
        count_chunks = await conn.fetchval("SELECT COUNT(*) FROM document_chunks WHERE is_active = TRUE;")
        print(f"📊 Veritabanı Özeti: {count_docs} Aktif Doküman, {count_chunks} Vektör Parçası.")
        
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(seed_data())
