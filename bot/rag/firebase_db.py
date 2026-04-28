import logging
import os
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
        cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
        if not os.path.exists(cred_path):
            logger.warning(f"Файл ключей Firebase '{cred_path}' не найден! База данных не подключена.")
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
