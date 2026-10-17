from pathlib import Path

def load_template(template_path):
    """Load the template file from the given path."""
    if not Path(template_path).exists():
        raise FileNotFoundError(f"Template file does not exist: {template_path}")
    
    with open(template_path, 'r') as file:
        return file.read()

def save_template(content, output_path):
    """Save the modified content to the output file."""
    with open(output_path, 'w') as file:
        file.write(content)

def replace_placeholder(template, placeholder, value):
    """Replace the specified placeholder in the template with the given value."""
    return template.replace(placeholder, value)

def prepare_template(template_path, output_path, replacements):
    """
    Prepare the template by loading it, replacing placeholders, and saving it.
    
    Args:
        template_path (str): The path to the template file.
        output_path (str): The path to save the modified template.
        replacements (dict): A dictionary of placeholders and their replacement values.
    """
    template = load_template(template_path)
    
    for placeholder, value in replacements.items():
        template = replace_placeholder(template, placeholder, value)

    save_template(template, output_path)
