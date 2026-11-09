# SmartShev для Home Assistant

[![HACS validation](https://github.com/AlexMarvel/smartshev-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/AlexMarvel/smartshev-ha/actions/workflows/validate.yml)
[![GitHub release](https://img.shields.io/github/v/release/AlexMarvel/smartshev-ha)](https://github.com/AlexMarvel/smartshev-ha/releases/latest)

**SmartShev** підключає пристрої екосистеми SmartShev до Home Assistant. Перший підтримуваний тип — енергетичні шлюзи. Вони передають показники інвертора й акумулятора, а набір сенсорів визначає активний профіль. Підтримку інших типів пристроїв додаватимемо окремо.

## Встановлення

Потрібен Home Assistant 2024.8 або новіший. Для підключення енергетичного шлюзу використовуйте актуальну прошивку.

[![Відкрити репозиторій у HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=AlexMarvel&repository=smartshev-ha&category=integration)

1. Додайте `https://github.com/AlexMarvel/smartshev-ha` у **HACS → Інтеграції → Користувацькі репозиторії** як інтеграцію.
2. Встановіть **SmartShev** і перезапустіть Home Assistant.
3. Відкрийте **Налаштування → Пристрої та служби**. Виберіть знайдений шлюз або натисніть **Додати інтеграцію → SmartShev**.

### Ручне встановлення

Скопіюйте [папку компонента](custom_components/shevlogger) цілком до `/config/custom_components/` і перезапустіть Home Assistant.

## Підключення енергетичного шлюзу

| Спосіб | Що доступно | Що потрібно |
|---|---|---|
| **Локальна мережа (LAN)** | Сенсори та доступні для запису параметри профілю, навіть без інтернету | Home Assistant має бачити шлюз у LAN. Потрібен приватний LAN-ключ від власника |
| **Хмара SmartShev** | Сенсори вибраного шлюзу через HTTPS, зокрема з іншої мережі | Акаунт SmartShev, увімкнений віддалений доступ і тариф Full власника шлюзу. Потрібна версія 0.4.0 або новіша |

### Через акаунт SmartShev

1. У Home Assistant виберіть **Додати інтеграцію → SmartShev → Увійти в SmartShev**.
2. Відкрийте посилання, увійдіть і підтвердьте підключення. Поверніться в Home Assistant і продовжіть.
3. Оберіть шлюз, потім **Локально (LAN)** або **Хмара SmartShev**. Якщо Home Assistant не знаходить шлюз локально, вкажіть його приватну IP-адресу.

### Локально вручну

У SmartShev відкрийте **Шлюз → Фічі → Інтеграції → Home Assistant** і скопіюйте LAN-ключ. У Home Assistant виберіть **Локально вручну** та введіть адресу й ключ. Для шлюзу, знайденого в локальній мережі через mDNS, достатньо ключа.

Для повторної авторизації хмарного запису відкрийте наявне підключення
в Home Assistant і підтвердьте вхід у SmartShev. Хмарний доступ відкликається
в активних сеансах акаунта SmartShev.

## Оновлення після перейменування репозиторію

Актуальна адреса для HACS — `https://github.com/AlexMarvel/smartshev-ha`. Якщо у вас версія 0.3.14, інтеграція може відображатися під попередньою назвою. Після оновлення до **SmartShev** наявні пристрої, сутності й автоматизації продовжують використовувати свої ідентифікатори.

[SmartShev](https://smartshev.pp.ua) · [Повідомити про проблему](https://github.com/AlexMarvel/smartshev-ha/issues)
