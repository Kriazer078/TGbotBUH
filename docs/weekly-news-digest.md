# Понедельничная подборка новостей

Бот работает через Telegram webhook и автоматически публикует до пяти
проверенных новостей экономики и бизнеса каждый понедельник в 09:30 по времени
Алматы.

## Настройки

```env
NEWS_TARGET_CHAT_ID=-1002318310296
NEWS_TARGET_THREAD_ID=1
NEWS_SCHEDULE_DAY=mon
NEWS_SCHEDULE_TIME=09:30
NEWS_TIMEZONE=Asia/Almaty
NEWS_ADMIN_TEST_MODE=false
NEWS_AI_TIMEOUT_SECONDS=30
NEWS_CLAIM_LEASE_MINUTES=30
INTERNAL_TICK_SECRET=<случайный секрет>
NEWS_ALLOWED_DOMAINS=nationalbank.kz,kgd.gov.kz,gov.kz,stat.gov.kz,uchet.kz,forbes.kz,kursiv.media,kapital.kz
NEWS_WORLD_DOMAINS=reuters.com,bloomberg.com,ft.com,worldbank.org,imf.org
```

Также должны быть настроены `BOT_TOKEN`, `WEBHOOK_HOST`, `GOOGLE_API_KEY`,
`GEMINI_MODEL`, доступ к Firebase и `ADMIN_ID`.

Бот должен состоять в целевой группе и иметь право отправлять сообщения в
указанную тему.

## Telegram-источники

Основные свежие материалы бот читает с публичных страниц каналов
`@prg_jur` (ZANGER | PRG) и `@commentariuskz` (Комментарий). Принимаются только
текстовые публикации с точным временем за последние 24 часа. В подборку
попадают новости экономики, бизнеса, финансов и значимые изменения правил для
компаний. Ссылка ведёт на конкретный Telegram-пост.

Если один канал временно недоступен или подходящих материалов недостаточно,
бот дополняет список проверенными сайтами из `NEWS_ALLOWED_DOMAINS` и
`NEWS_WORLD_DOMAINS`.

## Проверка перед включением

- `/send_news` — сформировать подборку и показать только администратору.
- `/send_news live` — опубликовать подборку в целевой теме.

Успешные публикации записываются в коллекцию Firebase
`news_digest_publications`, поэтому повторная публикация за ту же дату
блокируется.

## Запуск в Cloud Run

В production понедельничную публикацию запускает Google Cloud Scheduler, а не
внутренний APScheduler процесса. Поэтому Cloud Run может безопасно уменьшать
число экземпляров до нуля между запросами.

- Задание: `weekly-business-digest`
- Расписание: `30 9 * * 1`
- Часовой пояс: `Asia/Almaty`
- Метод и путь: `POST /internal/weekly-digest`
- Защита: заголовок `X-Internal-Secret` со значением `INTERNAL_TICK_SECRET`

При временной ошибке Cloud Scheduler повторяет запрос. Атомарная запись в
`news_digest_publications` не позволяет повторно отправить выпуск за ту же
дату. Внутренний APScheduler продолжает только обновлять новостной кэш каждые
шесть часов.
