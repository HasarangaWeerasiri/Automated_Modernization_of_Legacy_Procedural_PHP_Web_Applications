# Legacy runtime for any benchmark application. The application source is mounted
# read-only at run time; nothing app-specific is baked into this image.
FROM php:7.4-apache

# The base image ships with no active php.ini, which leaves output_buffering off.
# Legacy procedural apps are written against a stock configuration where it is on
# (they call session_start() and header() after emitting HTML), so PHP's own
# shipped production configuration is activated as the baseline.
RUN docker-php-ext-install mysqli \
 && cp "$PHP_INI_DIR/php.ini-production" "$PHP_INI_DIR/php.ini"

COPY php.ini /usr/local/etc/php/conf.d/zz-c4-legacy.ini
