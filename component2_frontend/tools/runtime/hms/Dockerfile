# Legacy side of the Phase 4 runtime check: PHP 7.4 + Apache with mysqli (HMS uses mysqli_* only).
FROM php:7.4-apache
RUN docker-php-ext-install mysqli
COPY php.ini /usr/local/etc/php/conf.d/zz-c2-runtime.ini
RUN mkdir -p /var/log/php && chown www-data:www-data /var/log/php
