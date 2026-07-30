"""Integrations: external service adapters and the contracts they satisfy.

Modules in this package isolate third-party service code so the rest of the
pipeline never imports a provider SDK directly. Each module owns the protocol
its adapter implements and the payload dataclasses it accepts.
"""
