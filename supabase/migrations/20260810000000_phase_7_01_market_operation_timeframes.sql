-- ADT Phase 7-01A1: align the backend-only market operation catalog with the
-- canonical timeframe registry used by the application.
--
-- The Phase 2D catalog originally allowed only 1m, 5m, 1h and 1d. Phase 6
-- expanded the canonical registry to include 15m, 30m and 4h. Operational
-- submissions must accept the same closed registry as the application.

alter table public.market_data_operations
    drop constraint market_data_operations_identity_check;

alter table public.market_data_operations
    add constraint market_data_operations_identity_check
        check (
            exchange = 'binance'
            and market = 'spot'
            and symbol ~ '^[A-Z0-9][A-Z0-9._-]{0,31}/[A-Z0-9][A-Z0-9._-]{0,31}$'
            and split_part(symbol, '/', 1) <> split_part(symbol, '/', 2)
            and timeframe in (
                '1m',
                '5m',
                '15m',
                '30m',
                '1h',
                '4h',
                '12h',
                '1d',
                '1w'
            )
            and dataset_id ~ '^[A-Za-z0-9_-]{1,192}$'
            and dataset_id = rtrim(
                translate(
                    replace(
                        encode(
                            convert_to(
                                exchange || ':' || market || ':' || symbol || ':' || timeframe,
                                'UTF8'
                            ),
                            'base64'
                        ),
                        chr(10),
                        ''
                    ),
                    '+/',
                    '-_'
                ),
                '='
            )
        );
