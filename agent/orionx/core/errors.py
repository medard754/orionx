"""Erreurs métier d'ORIONX."""


class OrionxError(Exception):
    """Classe de base."""


class ConfigError(OrionxError):
    """Configuration invalide ou hors des bornes de sécurité."""


class ApprovalError(OrionxError):
    """Approbation de risque absente, falsifiée ou expirée."""


class AccountChangedError(OrionxError):
    """Le compte connecté au terminal n'est plus celui du démarrage (ou n'est plus un compte démo)."""


class InvalidQuoteError(OrionxError):
    """Cotation du courtier inutilisable (bid/ask incohérents) : on attend la suivante, jamais de prix inventé.

    Distincte de GatewayError : la liaison fonctionne, aucune reconnexion ne doit être tentée."""


class GatewayError(OrionxError):
    """Erreur de communication avec le courtier / MetaTrader 5."""
