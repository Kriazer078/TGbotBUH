import logging
import os
import uuid
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import numpy as np

logger = logging.getLogger(__name__)

# Глобальная переменная для базы
db = None
# Кэш для быстрого поиска (чтобы не скачивать всю базу на каждый запрос пользователя)
# Для Налогового кодекса (около 1000 статей) кэш в памяти займет пару мегабайт, что очень эффективно.
_vector_cache = []

def init_firebase():
    """
    Инициализирует подключение к Firebase.
    """
    global db
    if not firebase_admin._apps:
        # Пытаемся загрузить из JSON-строки в переменной окружения (удобно для Render)
        cred_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
        if cred_json:
            try:
                import json
                cred_dict = json.loads(cred_json)
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred)
                db = firestore.client()
                logger.info("Успешное подключение к Firebase Firestore (через JSON ENV).")
                return True
            except Exception as e:
                logger.error(f"Ошибка при инициализации Firebase через JSON ENV: {e}")
                # Если не получилось через ENV, идем дальше к файлу

        cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
        if not os.path.exists(cred_path):
            logger.warning(f"Файл ключей Firebase '{cred_path}' не найден и FIREBASE_CREDENTIALS_JSON не задан! База данных не подключена.")
            return False
            
        try:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            logger.info("Успешное подключение к Firebase Firestore.")
            return True
        except Exception as e:
            logger.error(f"Ошибка при инициализации Firebase: {e}")
            return False
    return True

async def save_articles_to_firebase(articles_with_embeddings):
    """
    Сохраняет статьи и их векторы (эмбеддинги) в коллекцию Firestore.
    """
    if not db:
        logger.error("База Firebase не инициализирована.")
        return False
        
    try:
        batch = db.batch()
        collection_ref = db.collection('knowledge_base')
        
        # Для простоты полностью перезаписываем или обновляем по ID (например, номеру статьи)
        for i, item in enumerate(articles_with_embeddings):
            # В качестве ID можно использовать "tax_code_article_N"
            doc_ref = collection_ref.document(f"article_{i}")
            batch.set(doc_ref, {
                "title": item["title"],
                "text": item["text"],
                "url": item.get("url", ""),
                "embedding": item["embedding"] # массив float
            })
            
            # Если батч слишком большой (лимит 500), нужно отправлять частями
            if (i + 1) % 400 == 0:
                batch.commit()
                batch = db.batch()
                
        batch.commit()
        logger.info(f"В Firebase успешно сохранено {len(articles_with_embeddings)} статей.")
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении в Firebase: {e}")
        return False

def cosine_similarity(v1, v2):
    """Вычисляет косинусное сходство между двумя векторами."""
    dot_product = np.dot(v1, v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    return dot_product / (norm_v1 * norm_v2)

async def search_similar_articles(query_embedding, top_k=3):
    """
    Ищет наиболее подходящие статьи в Firebase.
    """
    global _vector_cache
    if not db:
        return []

    # Если кэш пуст, загружаем базу в память (это делается один раз)
    if not _vector_cache:
        docs = db.collection('knowledge_base').stream()
        for doc in docs:
            data = doc.to_dict()
            if "embedding" in data:
                _vector_cache.append(data)
        logger.info(f"Загружено {len(_vector_cache)} документов в кэш для поиска.")

    if not _vector_cache:
        return []

    # Вычисляем сходство запроса с каждой статьей
    results = []
    for item in _vector_cache:
        sim = cosine_similarity(query_embedding, item["embedding"])
        results.append((sim, item))
        
    # Сортируем по убыванию сходства и берем top_k
    results.sort(key=lambda x: x[0], reverse=True)
    
    top_results = []
    for sim, item in results[:top_k]:
        top_results.append({
            "title": item["title"],
            "text": item["text"],
            "url": item.get("url", ""),
            "score": sim
        })
        
    return top_results

async def add_learned_text(text: str, embedding: list) -> bool:
    """Сохраняет новую информацию (обученную админом) в knowledge_base."""
    global _vector_cache
    if not db:
        return False
    try:
        import uuid
        doc_id = f"learned_{uuid.uuid4().hex[:8]}"
        data = {
            "title": "Обучено администратором",
            "text": text,
            "url": "admin_learn",
            "embedding": embedding
        }
        db.collection('knowledge_base').document(doc_id).set(data)
        
        # Обновляем кэш
        _vector_cache.append(data)
        logger.info(f"Добавлено новое правило от админа: {doc_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении обученного текста: {e}")
        return False


def get_recent_news(limit: int = 5) -> list[dict]:
    """
    Возвращает последние `limit` новостей из коллекции 'news_feed'.
    Используется для обогащения контекста ИИ свежей информацией.
    """
    if not db:
        return []
    try:
        docs = (
            db.collection("news_feed")
            .order_by("saved_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        news = []
        for doc in docs:
            data = doc.to_dict()
            news.append({
                "title": data.get("title", ""),
                "text": data.get("text", ""),
                "url": data.get("url", ""),
                "source": data.get("source", ""),
                "date": data.get("date", ""),
            })
        logger.info(f"[firebase_db] Загружено {len(news)} свежих новостей из news_feed.")
        return news
    except Exception as e:
        logger.error(f"[firebase_db] Ошибка при получении новостей: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# ХРАНЕНИЕ ДИАЛОГОВ И ОБРАТНАЯ СВЯЗЬ
# ══════════════════════════════════════════════════════════════════════════════

def save_dialog(user_id: int, user_text: str, bot_answer: str,
                thread_id: int = None, embedding: list = None) -> str | None:
    """
    Сохраняет пару вопрос-ответ в коллекцию 'dialogs'.
    Возвращает doc_id для последующей оценки.
    """
    if not db:
        return None
    try:
        doc_id = f"dlg_{uuid.uuid4().hex[:12]}"
        data = {
            "user_id":   user_id,
            "thread_id": thread_id,
            "question":  user_text,
            "answer":    bot_answer,
            "rating":    None,        # None / "good" / "bad"
            "embedding": embedding,   # вектор вопроса (для похожих запросов)
            "created_at": datetime.utcnow(),
        }
        db.collection("dialogs").document(doc_id).set(data)
        logger.info(f"[dialog] Сохранён диалог {doc_id} от user {user_id}")
        return doc_id
    except Exception as e:
        logger.error(f"[dialog] Ошибка сохранения диалога: {e}")
        return None


def update_dialog_rating(doc_id: str, rating: str) -> bool:
    """
    Обновляет оценку диалога ('good' или 'bad').
    Если оценка 'bad' — добавляем в knowledge_base как «проблемный вопрос» для дообучения.
    """
    if not db:
        return False
    try:
        doc_ref = db.collection("dialogs").document(doc_id)
        doc_ref.update({"rating": rating, "rated_at": datetime.utcnow()})

        if rating == "bad":
            # Помечаем как «требует проверки» — админ может исправить через /learn
            data = doc_ref.get().to_dict()
            if data:
                db.collection("dialogs_review").document(doc_id).set({
                    "question":   data.get("question", ""),
                    "bad_answer": data.get("answer", ""),
                    "user_id":    data.get("user_id"),
                    "created_at": data.get("created_at"),
                    "flagged_at": datetime.utcnow(),
                    "status":     "pending",  # pending / fixed
                })
                logger.info(f"[dialog] Диалог {doc_id} помечен как 'bad' → отправлен на проверку")
        return True
    except Exception as e:
        logger.error(f"[dialog] Ошибка обновления рейтинга: {e}")
        return False


def get_similar_dialogs(query_embedding: list, top_k: int = 2) -> list[dict]:
    """
    Находит похожие прошлые диалоги с хорошей оценкой (rating='good').
    Используется как контекст для RAG — «обучение на опыте».
    """
    if not db or not query_embedding:
        return []
    try:
        # Берём только хорошие ответы (оценены пользователями)
        docs = (
            db.collection("dialogs")
            .where("rating", "==", "good")
            .where("embedding", "!=", None)
            .limit(200)
            .stream()
        )
        candidates = []
        for doc in docs:
            d = doc.to_dict()
            if d.get("embedding"):
                candidates.append(d)

        if not candidates:
            return []

        q = np.array(query_embedding)
        scored = []
        for c in candidates:
            v = np.array(c["embedding"])
            sim = float(np.dot(q, v) / (np.linalg.norm(q) * np.linalg.norm(v) + 1e-10))
            scored.append((sim, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = []
        for sim, c in scored[:top_k]:
            if sim > 0.75:   # только очень похожие вопросы
                result.append({
                    "question": c.get("question", ""),
                    "answer":   c.get("answer", ""),
                    "score":    sim,
                })
        return result
    except Exception as e:
        logger.error(f"[dialog] Ошибка поиска похожих диалогов: {e}")
        return []


def get_pending_reviews(limit: int = 10) -> list[dict]:
    """Возвращает список диалогов, помеченных как 'плохие' (для /review админа)."""
    if not db:
        return []
    try:
        docs = (
            db.collection("dialogs_review")
            .where("status", "==", "pending")
            .order_by("flagged_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [d.to_dict() | {"id": d.id} for d in docs]
    except Exception as e:
        logger.error(f"[dialog] Ошибка получения проблемных диалогов: {e}")
        return []
