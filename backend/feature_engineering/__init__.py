"""Config + Redis state shared by the Velocity and Geo agents.

Only the runtime hot-path helpers live here now: ``config`` (feature_config.yaml
loader), ``redis_client`` (Redis state + ``RedisUnavailable``) and ``geo_math``
(haversine). The historical batch feature-engineering pipeline has been removed;
the agents do not depend on it.
"""
