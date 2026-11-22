"""Small helpers that work on every supported Odoo series.

Odoo 20 replaced ir.config_parameter get_param/set_param with typed getters and setters
(get_str, set_str, get_bool and so on). These helpers use whichever the running Odoo has.
"""


def param_get(env, key):
    """The stored string, or False when unset."""
    params = env["ir.config_parameter"].sudo()
    if hasattr(params, "get_param"):
        return params.get_param(key) or False
    return params.get_str(key) or False


def param_set(env, key, value):
    """Store a string. False or None clears the value."""
    params = env["ir.config_parameter"].sudo()
    value = value if value not in (None, False, "") else False
    if hasattr(params, "set_param"):
        return params.set_param(key, value)
    return params.set_str(key, value)


def param_bool(env, key):
    value = param_get(env, key)
    return str(value).strip().lower() in ("1", "true", "yes", "on")
