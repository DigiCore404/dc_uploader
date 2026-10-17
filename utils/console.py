"""Short worker log headings, shared by CLI output and browser history."""


def print_section(name, version=None):
    if name == 'Header':
        print(f'DigitalCore Uploader {version or ""}'.strip(), flush=True)
        print('Original DC Uploader backend by R4bb1t & UnpooledUnit.', flush=True)
    else:
        print(f'\n--- {name} ---', flush=True)
