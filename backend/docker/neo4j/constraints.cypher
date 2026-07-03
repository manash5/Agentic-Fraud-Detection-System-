CREATE CONSTRAINT user_id_unique IF NOT EXISTS
FOR (user:User)
REQUIRE user.user_id IS UNIQUE;

CREATE CONSTRAINT device_id_unique IF NOT EXISTS
FOR (device:Device)
REQUIRE device.device_id IS UNIQUE;

CREATE CONSTRAINT merchant_id_unique IF NOT EXISTS
FOR (merchant:Merchant)
REQUIRE merchant.merchant_id IS UNIQUE;

CREATE CONSTRAINT transaction_id_unique IF NOT EXISTS
FOR (transaction:Transaction)
REQUIRE transaction.transaction_id IS UNIQUE;
