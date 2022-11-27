# О проекте
Реализован рекурсивный DNS резолвер, который самостоятельно обходит авторитативные DNS сервера, начиная с корневых. Кэширование осуществляется в соответствии с полем TTL. Количество кэшируемых записей является параметром.

Взаимодействие с резолвером реализовано через API (Flask), которое поддерживает следующие методы:

- `/get-records?domain=&trace=` (при пропуске `trace` поведение аналогично `trace=false`) - реализует описанную выше логику. Если выставлен флаг `trace=true`, то, игнорируя закэшированные данные,резолвер пройти всю цепочку DNS серверов, возвращая не только A и АААА записи, но и список авторитативных серверов.
- `/update-cache` - актуализирует кэш в соответствии со временем вызова метода (по умолчанию кэш ленивый и обновляется только при запросах добавления/обращения элемента).

Разбор сообщений и общая спецификация реализованы с опорой на [переведённую документацию RFC 1035](https://efim360.ru/rfc-1035-domennye-imena-realizatsiya-i-spetsifikatsiya/#4-1-3-Resource-record-format).

Поля `DNSSEC` в рамках текущей реализации не проверяются (т.е. по умолчанию мы доверяем выдаче).

# Запуск
### Linux/MacOS
```
git clone https://github.com/noath/dns-resolver.git
cd dns-resolver
python3 -m dns_resolver
```
### Windows
```
git clone https://github.com/noath/dns-resolver.git
cd dns-resolver
python -m dns_resolver
```


Также при запуске можно указать параметр, соответствующий максимальному кол-ву кэшируемых записей (по умолчанию, без ограничения).
### Linux/MacOS
```
git clone https://github.com/noath/dns-resolver.git
cd dns-resolver
python3 -m dns_resolver 10
```
### Windows
```
git clone https://github.com/noath/dns-resolver.git
cd dns-resolver
python -m dns_resolver 10
```

После запуска резолвер будет доступен по адресу http://127.0.0.1:5000 *(адрес и порт задаются в constants.py, при необходимости их можно поменять)*.

# Примеры работы
### Из консоли
```
- curl "http://127.0.0.1:5000/get-records?domain=lavka.yandex.&trace=true"
- Using IPv4 only.<br/><br/>Trace:<br/>a.root-servers.net 198.41.0.4<br/>ns3.uniregistry.net 185.159.197.3<br/>a.root-servers.net 198.41.0.4<br/>a.dns.ripn.net 193.232.128.6<br/>ns2.yandex.RU 93.158.134.1<br/>ns4.yandex.ru 77.88.21.1<br/><br/>Answers:<br/>lavka.yandex 87.250.250.116

- curl "http://127.0.0.1:5000/get-records?domain=ya.ru&trace=true"
- Using IPv4 only.<br/><br/>Trace:<br/>a.root-servers.net 198.41.0.4<br/>a.dns.ripn.net 193.232.128.6<br/>ns2.yandex.RU 93.158.134.1<br/><br/>Answers:<br/>ya.ru 87.250.250.242

- curl "http://127.0.0.1:5000/get-records?domain=google.com"
- Using IPv4 only.<br/><br/>google.com 142.250.74.206

- curl "http://127.0.0.1:5000/get-records?domain=noath-non-existent-domain.ru"
- There are not A or AAAA records for this domain.
```

### Из браузера
После запуска те же примеры доступны по следующим ссылкам, что указаны в аргументах curl в прошлой секции.