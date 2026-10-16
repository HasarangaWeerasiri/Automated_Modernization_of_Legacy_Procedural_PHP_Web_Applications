# Phase 4e runtime check, legacy side: phpBlog at 4ffe3c9 on PHP 8.2 + Apache with mysqli. phpBlog's own .htaccess
# (mod_rewrite: URLs without ".php") is honoured; no phpBlog file is edited (tools/runtime/phpblog/README.md).
FROM php:8.2-apache
RUN docker-php-ext-install mysqli && a2enmod rewrite \
    && sed -i 's/AllowOverride None/AllowOverride All/' /etc/apache2/apache2.conf
COPY php.ini /usr/local/etc/php/conf.d/zz-c2-runtime.ini
RUN mkdir -p /var/log/php && chown www-data:www-data /var/log/php
