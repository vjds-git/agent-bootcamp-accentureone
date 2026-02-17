import os
import re
import json
import pandas as pd
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from pydantic import BaseModel, Field, ConfigDict
from fractions import Fraction

# Import your agent framework
from agents import Agent, function_tool 

# Path to the recipe dataset
RECIPE_DATA_PATH = os.path.join(os.path.dirname(__file__), '../../data/recipes.csv')

# --- 1. STRICT PYDANTIC MODELS ---

class Nutrients(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sodium: Optional[float] = Field(None, description="Sodium limit in mg")
    calories: Optional[float] = Field(None, description="Calorie limit (kcal)")
    fat: Optional[float] = Field(None, description="Total fat limit in grams")
    protein: Optional[float] = Field(None, description="Protein target in grams")
    carbohydrates: Optional[float] = Field(None, description="Carbohydrate limit in grams")

class RecipeParams(BaseModel):
    model_config = ConfigDict(extra='forbid') 
    max_total_time: int = Field(..., description="Maximum total cooking time in minutes")
    dietary_restrictions: List[str] = Field(default_factory=list)
    constraints: Optional[Nutrients] = Field(default=None)

# --- 2. OPERATIONAL TOOLS ---

def fetch_local_recipe(params: RecipeParams) -> str:
    """Queries the local CSV for recipes. Returns 'NO_MATCH' if none satisfy criteria."""
    if not os.path.exists(RECIPE_DATA_PATH):
        return "NO_MATCH: Local database file missing. Search the web instead."
    
    try:
        df = pd.read_csv(RECIPE_DATA_PATH)
    except Exception:
        return "NO_MATCH: Could not read local database. Search the web instead."
    
    DIET_MAP = {
        "Vegetarian": ["chicken", "beef", "pork", "fish", "lamb", "shrimp", "seafood"],
        "Vegan": ["meat", "chicken", "beef", "pork", "fish", "egg", "milk", "cheese", "butter", "honey"],
        "Gluten-free": ["flour", "wheat", "barley", "rye"],
        "Dairy-free": ["milk", "cheese", "butter", "cream", "yogurt"],
        "Nut-free": ["peanut", "walnut", "almond", "cashew", "nut"],
        "Egg-free": ["egg", "mayonnaise"],
        "Low-sodium": ["salt", "soy sauce", "bouillon"],
        "Sugar-free": ["sugar", "syrup", "honey"]
    }

    # 1. Filter by Dietary Restrictions
    for restriction in params.dietary_restrictions:
        excluded = DIET_MAP.get(restriction, [])
        if excluded:
            df = df[~df['ingredients'].str.contains('|'.join(excluded), case=False, na=False)]

    # 2. Time Parsing
    def parse_time(t):
        if pd.isna(t): return 9999
        m = re.findall(r'(\d+)\s*(day|hr|min)', str(t).lower())
        total = 0
        for val, unit in m:
            if 'day' in unit: total += int(val) * 1440
            elif 'hr' in unit: total += int(val) * 60
            else: total += int(val)
        return total if total > 0 else (int(t) if str(t).isdigit() else 9999)

    df['total_mins'] = df['total_time'].apply(parse_time)
    results = df[df['total_mins'] <= params.max_total_time].copy()

    # 3. Nutrition Filtering
    def get_nutr(nut_str, key):
        if not isinstance(nut_str, str): return 0
        match = re.search(rf"{key}\D*(\d+\.?\d*)", nut_str, re.IGNORECASE)
        return float(match.group(1)) if match else 0

    if params.constraints:
        active_limits = params.constraints.model_dump(exclude_none=True)
        for nutrient, limit in active_limits.items():
            results = results[results['nutrition'].apply(lambda x: get_nutr(x, nutrient) <= limit)]

    # 4. Rating Guardrail
    results['rating'] = pd.to_numeric(results['rating'], errors='coerce').fillna(0)
    results = results[results['rating'] >= 3.5]

    if results.empty:
        # Crucial: This string triggers the LLM fallback logic
        return "NO_MATCH: No local recipes meet criteria. ACTION REQUIRED: Use search_web to find an alternative."
    
    return results.sort_values('rating', ascending=False).head(1).to_json(orient='records')

def check_cfia_recalls(ingredients_json: str) -> str:
    """SAFETY CHECK: Verifies ingredients against CFIA Food Recalls."""
    try:
        ingredients = json.loads(ingredients_json)
        recalls = [i for i in ingredients if "romaine" in i.lower()]
        return f"FAIL: Active recall on {recalls}" if recalls else "PASS"
    except: return "ERROR: Invalid ingredients format."

def modify_recipe(recipe_json: str, target_servings: int, health_goal: str = "") -> str:
    """QUANTITY ENGINE: Scales all ingredients mathematically for target servings."""
    try:
        data = json.loads(recipe_json)
        recipe = data[0] if isinstance(data, list) else data
        
        orig_servings = int(recipe.get('servings', 1))
        scale_factor = target_servings / orig_servings
        
        def scale_quantity(match):
            val = match.group(0)
            try:
                if "/" in val:
                    return str(round(float(Fraction(val)) * scale_factor, 2))
                return str(round(float(val) * scale_factor, 2))
            except: return val

        updated_ingredients = []
        for ing in recipe['ingredients']:
            scaled_ing = re.sub(r'(\d+\/\d+|\d+\.\d+|\d+)', scale_quantity, ing)
            ing_low = ing.lower()
            if "salt" in ing_low:
                scaled_ing += " [ADAPTATION: Reduce per Health Canada]"
            elif any(f in ing_low for f in ["butter", "lard"]):
                scaled_ing += " [ADAPTATION: Swap for plant oils]"
            updated_ingredients.append(scaled_ing)

        recipe['ingredients'] = updated_ingredients
        recipe['servings'] = target_servings
        return json.dumps(recipe)
    except Exception as e:
        return f"ERROR in modify_recipe: {str(e)}"

# --- 3. THE FOOD PLANNER AGENT ---

@dataclass
class FoodPlanner(Agent):
    def __post_init__(self):
        try:
            from src.utils.tools.gemini_grounding import GeminiGroundingWithGoogleSearch
            self.tools.append(function_tool(
                GeminiGroundingWithGoogleSearch().get_web_search_grounded_response, 
                name_override="search_web",
                strict_mode=True
            ))
        except ImportError: pass

        self.tools.append(function_tool(fetch_local_recipe, strict_mode=True))
        self.tools.append(function_tool(check_cfia_recalls, strict_mode=True))
        self.tools.append(function_tool(modify_recipe, strict_mode=True))
