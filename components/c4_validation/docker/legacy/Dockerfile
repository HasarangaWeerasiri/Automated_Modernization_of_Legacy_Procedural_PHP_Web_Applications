# Legacy runtime for any benchmark application. The application source is mounted
# read-only at run time; nothing app-specific is baked into this image.
#
# Each benchmark chooses the PHP version it was written for (C4_LEGACY_PHP) and the
# database extensions it calls (C4_LEGACY_PHP_EXTENSIONS): an application written
# for PHP 5 with the old mysql_* functions cannot run on PHP 7, and changing its
# code would be a repair. Choosing its runtime is configuration.
ARG PHP_VERSION=7.4
FROM php:${PHP_VERSION}-apache

ARG PHP_EXTENSIONS=mysqli

# The base image ships with no active php.ini, which leaves output_buffering off.
# Legacy procedural apps are written against a stock configuration where it is on
# (they call session_start() and header() after emitting HTML), so PHP's own
# shipped production configuration is activated as the baseline.
RUN docker-php-ext-install ${PHP_EXTENSIONS} \
 && cp "$PHP_INI_DIR/php.ini-production" "$PHP_INI_DIR/php.ini"

COPY php.ini /usr/local/etc/php/conf.d/zz-c4-legacy.ini
