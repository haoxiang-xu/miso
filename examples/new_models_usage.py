#!/usr/bin/env python3
"""
Example demonstrating usage of newly added model configurations.

This example shows how to:
1. Import and use the new model configurations
2. Access model capabilities and default payloads
3. Create custom model configurations based on the existing ones
"""

from miso.schemas.models import (
    GPT_4O, 
    CLAUDE_SONNET_35, 
    GEMINI_PRO_15,
    ModelConfiguration,
    ModelCapabilities,
    ModelDefaultPayload
)


def demonstrate_model_usage():
    """Demonstrate basic usage of the new model configurations."""
    
    print("=== New Model Configurations Demo ===\n")
    
    # GPT-4o Model
    print(f"GPT-4o Configuration:")
    print(f"  Name: {GPT_4O.name}")
    print(f"  Provider: {GPT_4O.capabilities.provider}")
    print(f"  Model: {GPT_4O.capabilities.provider_model}")
    print(f"  Max Context: {GPT_4O.capabilities.max_context_window_tokens:,} tokens")
    print(f"  Supports Tools: {GPT_4O.capabilities.supports_tools}")
    print(f"  Supports Response Format: {GPT_4O.capabilities.supports_response_format}")
    print(f"  Input Modalities: {', '.join(GPT_4O.capabilities.input_modalities)}")
    print(f"  Default Max Tokens: {GPT_4O.default_payload.payload['max_tokens']}")
    print(f"  Default Temperature: {GPT_4O.default_payload.payload['temperature']}")
    print()
    
    # Claude Sonnet 3.5 Model
    print(f"Claude Sonnet 3.5 Configuration:")
    print(f"  Name: {CLAUDE_SONNET_35.name}")
    print(f"  Provider: {CLAUDE_SONNET_35.capabilities.provider}")
    print(f"  Model: {CLAUDE_SONNET_35.capabilities.provider_model}")
    print(f"  Max Context: {CLAUDE_SONNET_35.capabilities.max_context_window_tokens:,} tokens")
    print(f"  Supports Reasoning: {CLAUDE_SONNET_35.capabilities.supports_reasoning}")
    print(f"  Input Modalities: {', '.join(CLAUDE_SONNET_35.capabilities.input_modalities)}")
    print(f"  Default Max Tokens: {CLAUDE_SONNET_35.default_payload.payload['max_tokens']}")
    print()
    
    # Gemini Pro 1.5 Model
    print(f"Gemini Pro 1.5 Configuration:")
    print(f"  Name: {GEMINI_PRO_15.name}")
    print(f"  Provider: {GEMINI_PRO_15.capabilities.provider}")
    print(f"  Model: {GEMINI_PRO_15.capabilities.provider_model}")
    print(f"  Max Context: {GEMINI_PRO_15.capabilities.max_context_window_tokens:,} tokens")
    print(f"  Input Modalities: {', '.join(GEMINI_PRO_15.capabilities.input_modalities)}")
    print(f"  Default Max Output Tokens: {GEMINI_PRO_15.default_payload.payload['max_output_tokens']}")
    print()


def demonstrate_model_serialization():
    """Demonstrate model serialization capabilities."""
    
    print("=== Model Serialization Demo ===\n")
    
    # Convert model to dictionary
    gpt4o_dict = GPT_4O.to_dict()
    print("GPT-4o as dictionary:")
    print(f"  Name: {gpt4o_dict['name']}")
    print(f"  Provider: {gpt4o_dict['capabilities']['provider']}")
    print(f"  Max Tokens: {gpt4o_dict['default_payload']['max_tokens']}")
    print()
    
    # Create model from dictionary
    custom_model = ModelConfiguration.from_dict(
        name="custom-gpt-4o",
        capabilities_data=gpt4o_dict['capabilities'],
        payload_data=gpt4o_dict['default_payload']
    )
    print(f"Custom model created from dictionary:")
    print(f"  Name: {custom_model.name}")
    print(f"  Provider: {custom_model.capabilities.provider}")
    print()


def create_custom_model():
    """Demonstrate creating a custom model configuration."""
    
    print("=== Custom Model Creation Demo ===\n")
    
    # Create a custom Ollama model configuration
    ollama_llama_model = ModelConfiguration(
        name="ollama-llama-3.2",
        capabilities=ModelCapabilities(
            provider="ollama",
            provider_model="llama3.2:latest",
            max_context_window_tokens=32768,
            supports_tools=True,
            supports_response_format=False,
            supports_previous_response_id=False,
            supports_reasoning=False,
            input_modalities=["text"],
            input_source_types={},
            allowed_payload_keys=["temperature", "top_p", "top_k", "repeat_penalty"]
        ),
        default_payload=ModelDefaultPayload(
            payload={
                "temperature": 0.8,
                "top_p": 0.9,
                "top_k": 40,
                "repeat_penalty": 1.1
            }
        )
    )
    
    print(f"Custom Ollama Model:")
    print(f"  Name: {ollama_llama_model.name}")
    print(f"  Provider: {ollama_llama_model.capabilities.provider}")
    print(f"  Model: {ollama_llama_model.capabilities.provider_model}")
    print(f"  Max Context: {ollama_llama_model.capabilities.max_context_window_tokens:,} tokens")
    print(f"  Default Temperature: {ollama_llama_model.default_payload.payload['temperature']}")
    print()


def compare_models():
    """Compare capabilities of different models."""
    
    print("=== Model Comparison ===\n")
    
    models = [GPT_4O, CLAUDE_SONNET_35, GEMINI_PRO_15]
    
    print("Context Window Comparison:")
    for model in sorted(models, key=lambda m: m.capabilities.max_context_window_tokens, reverse=True):
        print(f"  {model.name}: {model.capabilities.max_context_window_tokens:,} tokens")
    print()
    
    print("Multimodal Capabilities:")
    for model in models:
        modalities = ", ".join(model.capabilities.input_modalities)
        print(f"  {model.name}: {modalities}")
    print()
    
    print("Tool Support:")
    for model in models:
        tools = "✓" if model.capabilities.supports_tools else "✗"
        print(f"  {model.name}: {tools}")
    print()


if __name__ == "__main__":
    demonstrate_model_usage()
    demonstrate_model_serialization()
    create_custom_model()
    compare_models()
    
    print("=== Usage in Code ===")
    print("""
# Import models
from miso.schemas.models import GPT_4O, CLAUDE_SONNET_35, GEMINI_PRO_15

# Use in your application
selected_model = GPT_4O
print(f"Using {selected_model.name} with {selected_model.capabilities.max_context_window_tokens} tokens context")

# Access capabilities
if selected_model.capabilities.supports_tools:
    print("This model supports function calling")

# Get default parameters
default_params = selected_model.default_payload.to_dict()
print(f"Default parameters: {default_params}")
    """)