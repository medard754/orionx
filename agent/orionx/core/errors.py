"""Erreurs métier d'ORIONX."""


class OrionxError(Exception):
    """Classe de base."""


class ConfigError(OrionxError):
    """Configuration invalide ou hors des bornes de sécurité."""


class ApprovalError(OrionxError):
    """Approbation de risque absente, falsifiée ou expirée."""


class AccountChangedError(OrionxError):
    """Le compte connecté au terminal n'est plus celui du démarrage (ou n'est plus un compte démo)."""


class GatewayError(OrionxError):
    """Erreur de communication avec le courtier / MetaTrader 5."""
