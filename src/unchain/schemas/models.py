from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class ModelCapabilities:
    """Model capabilities configuration."""
    
    provider: str
    max_context_window_tokens: int
    supports_tools: bool = True
    supports_response_format: bool = False
    supports_previous_response_id: bool = False
    supports_reasoning: bool = False
    input_modalities: List[str] = field(default_factory=lambda: ["text"])
    input_source_types: Dict[str, List[str]] = field(default_factory=dict)
    allowed_payload_keys: List[str] = field(default_factory=list)
    provider_model: Optional[str] = None
    model_type: Optional[str] = None
    default_embedding_dimensions: Optional[int] = None
    supports_dimensions: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            "provider": self.provider,
            "max_context_window_tokens": self.max_context_window_tokens,
            "supports_tools": self.supports_tools,
            "supports_response_format": self.supports_response_format,
            "supports_previous_response_id": self.supports_previous_response_id,
            "supports_reasoning": self.supports_reasoning,
            "input_modalities": self.input_modalities,
            "input_source_types": self.input_source_types,
            "allowed_payload_keys": self.allowed_payload_keys,
        }
        
        if self.provider_model is not None:
            result["provider_model"] = self.provider_model
        if self.model_type is not None:
            result["model_type"] = self.model_type
        if self.default_embedding_dimensions is not None:
            result["default_embedding_dimensions"] = self.default_embedding_dimensions
        if self.supports_dimensions is not None:
            result["supports_dimensions"] = self.supports_dimensions
            
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelCapabilities":
        """Create from dictionary representation."""
        return cls(**data)


@dataclass
class ModelDefaultPayload:
    """Default payload configuration for a model."""
    
    payload: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return self.payload.copy()
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelDefaultPayload":
        """Create from dictionary representation."""
        return cls(payload=data)


@dataclass
class ModelConfiguration:
    """Complete model configuration including capabilities and defaults."""
    
    name: str
    capabilities: ModelCapabilities
    default_payload: ModelDefaultPayload
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "name": self.name,
            "capabilities": self.capabilities.to_dict(),
            "default_payload": self.default_payload.to_dict(),
        }
    
    @classmethod
    def from_dict(cls, name: str, capabilities_data: Dict[str, Any], payload_data: Dict[str, Any]) -> "ModelConfiguration":
        """Create from dictionary representations."""
        return cls(
            name=name,
            capabilities=ModelCapabilities.from_dict(capabilities_data),
            default_payload=ModelDefaultPayload.from_dict(payload_data)
        )


# Pre-defined model configurations
CLAUDE_HAIKU_35 = ModelConfiguration(
    name="claude-haiku-3.5",
    capabilities=ModelCapabilities(
        provider="anthropic",
        provider_model="claude-3-5-haiku-20241022",
        max_context_window_tokens=200000,
        supports_tools=True,
        supports_response_format=False,
        supports_previous_response_id=False,
        supports_reasoning=False,
        input_modalities=["text", "image"],
        input_source_types={
            "image": ["url", "base64"]
        },
        allowed_payload_keys=["max_tokens", "temperature", "top_k", "top_p"]
    ),
    default_payload=ModelDefaultPayload(
        payload={
            "max_tokens": 32000,
            "temperature": 0.7,
            "top_p": 1
        }
    )
)


__all__ = [
    "ModelCapabilities",
    "ModelDefaultPayload", 
    "ModelConfiguration",
    "CLAUDE_HAIKU_35"
]