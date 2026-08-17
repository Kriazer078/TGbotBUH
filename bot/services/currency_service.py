import logging
import requests
from datetime import datetime
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

NBK_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={date}"

# Кэш: {currency: (rate, date_str)}
_cache: dict = {}

SUPPORTED = {
    "USD": "🇺🇸 Доллар США",
    "EUR": "🇪🇺 Евро",
    "RUB": "🇷🇺 Российский рубль",
    "CNY": "🇨🇳 Юань",
    "GBP": "🇬🇧 Фунт стерлингов",
}


def get_rate(currency: str) -> tuple[float, int, str] | None:
    """
    Возвращает (rate_per_unit, quant, date_str) или None.
    rate_per_unit — курс одной единицы валюты в тенге.
    """
    currency = currency.upper()
    today = datetime.now().strftime("%d.%m.%Y")

    if currency in _cache and _cache[currency][2] == today:
        return _cache[currency]

    try:
        url = NBK_URL.format(date=today)
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item"):
            title = item.find("title")
            desc  = item.find("description")
            quant = item.find("quant")
            if title is None or title.text is None:
                continue
            if title.text.strip().upper() == currency:
                q = int(quant.text) if quant is not None and quant.text else 1
                rate_total = float(desc.text.replace(",", "."))
                rate_unit  = round(rate_total / q, 4)
                result = (rate_unit, q, today)
                _cache[currency] = result
                logger.info(f"[currency] {currency}: {rate_unit} тг (quant={q})")
                return result

        logger.warning(f"[currency] {currency} не найден в ответе НБК")
    except Exception as e:
        logger.error(f"[currency] Ошибка получения курса {currency}: {e}")
    return None


def format_rate_message(currency: str) -> str:
    """Возвращает готовое HTML-сообщение с курсом."""
    result = get_rate(currency)
    currency = currency.upper()
    name = SUPPORTED.get(currency, currency)

    if not result:
        return f"⚠️ Не удалось получить курс <b>{currency}</b>. Попробуйте позже."

    rate, quant, date = result
    if quant > 1:
        return (
            f"<b>{name} ({currency})</b>\n"
            f"<code>{quant} {currency} = {rate * quant:,.2f} тг</code>\n"
            f"<code>1 {currency} = {rate:,.4f} тг</code>\n"
            f"<i>Источник: НБК РК, {date}</i>"
        )
    return (
        f"<b>{name} ({currency})</b>\n"
        f"<code>1 {currency} = {rate:,.2f} тг</code>\n"
        f"<i>Источник: НБК РК, {date}</i>"
    )


def convert_to_tenge(amount: float, currency: str) -> str | None:
    """Конвертирует сумму в тенге. Возвращает HTML-строку или None."""
    result = get_rate(currency.upper())
    if not result:
        return None
    rate, _, date = result
    tenge = round(amount * rate, 2)
    return (
        f"<b>Конвертация {currency.upper()} → KZT</b>\n"
        f"<code>{amount:,.2f} {currency.upper()} = {tenge:,.2f} тг</code>\n"
        f"<i>Курс НБК: 1 {currency.upper()} = {rate:,.2f} тг ({date})</i>"
    )
