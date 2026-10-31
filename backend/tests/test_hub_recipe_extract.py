import json

from bt.hub.extract import extract_page


def test_recipe_instruction_sections_are_flattened_in_order():
    recipe = {
        '@type': 'Recipe', 'name': 'Soup', 'recipeIngredient': 'Water',
        'recipeInstructions': [
            {'@type': 'HowToSection', 'name': 'Prepare', 'itemListElement': [
                {'@type': 'HowToStep', 'text': 'Chop vegetables'},
                {'@type': 'HowToStep', 'name': 'Add water'},
            ]},
            'Simmer',
        ],
    }
    html = f'<script type="application/ld+json">{json.dumps(recipe)}</script>'.encode()
    item = extract_page(html, 'https://example.com/soup')
    assert item['meta']['ingredients'] == ['Water']
    assert item['meta']['instructions'] == ['Chop vegetables', 'Add water', 'Simmer']


def test_recipe_instruction_limit_applies_to_nested_sections():
    recipe = {'@type': 'Recipe', 'recipeInstructions': [{'itemListElement': ['Step'] * 100}]}
    html = f'<script type="application/ld+json">{json.dumps(recipe)}</script>'.encode()
    assert len(extract_page(html, 'https://example.com/recipe')['meta']['instructions']) == 60


def test_recipe_keeps_servings_nutrition_and_equipment():
    recipe = {
        '@type': 'Recipe', 'name': 'Bread', 'recipeYield': '1 loaf',
        'nutrition': {'@type': 'NutritionInformation', 'calories': '220 calories'},
        'tool': [{'@type': 'HowToTool', 'name': 'Dutch oven'}],
    }
    html = f'<script type="application/ld+json">{json.dumps(recipe)}</script>'.encode()
    meta = extract_page(html, 'https://example.com/bread')['meta']
    assert meta['servings'] == '1 loaf'
    assert meta['equipment'] == ['Dutch oven']


def test_howto_pattern_fields_are_normalized():
    pattern = {
        '@type': 'HowTo', 'name': 'Beginner scarf knitting pattern',
        'supply': [{'name': 'Wool yarn'}], 'tool': [{'name': '5 mm needles'}],
        'step': [{'text': 'Cast on stitches'}, {'text': 'Work rows'}],
        'skillLevel': 'Beginner', 'totalTime': 'PT2H',
    }
    html = f'<script type="application/ld+json">{json.dumps(pattern)}</script>'.encode()
    item = extract_page(html, 'https://example.com/scarf')
    assert item['type'] == 'pattern'
    assert item['meta']['materials'] == ['Wool yarn']
    assert item['meta']['tools'] == ['5 mm needles']
    assert item['meta']['instructions'] == ['Cast on stitches', 'Work rows']
    assert item['meta']['difficulty'] == 'Beginner'


def test_explicit_pattern_type_survives_without_pattern_word():
    pattern = {
        '@type': 'Pattern', 'name': 'Autumn scarf',
        'material': [{'name': 'Wool'}], 'step': [{'text': 'Cast on'}],
    }
    html = f'<script type="application/ld+json">{json.dumps(pattern)}</script>'.encode()
    item = extract_page(html, 'https://example.com/autumn-scarf')
    assert item['type'] == 'pattern'
    assert item['meta']['materials'] == ['Wool']
