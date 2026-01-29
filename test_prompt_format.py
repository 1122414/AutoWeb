from prompts.action_prompts import ACTION_CODE_GEN_PROMPT

try:
    formatted = ACTION_CODE_GEN_PROMPT.format(
        xpath_plan="Test Plan", 
        requirement="Test Req"
    )
    print("✅ Prompt formatted successfully.")
    # Check if {e} resulted in {e} literal
    if '{e}' in formatted and 'Warning: {e}' in formatted:
         print("✅ Braces preserved correctly.")
    else:
         print("❌ Braces verification failed.")
         print(formatted)

except KeyError as e:
    print(f"❌ KeyError: {e}")
except Exception as e:
    print(f"❌ Error: {e}")
