from agents import Agent, function_tool
from dataclasses import dataclass
from typing import Dict
import pandas as pd
import json
from pydantic import BaseModel
from typing import TypeVar


from src.utils.tools.gemini_grounding import GeminiGroundingWithGoogleSearch



recipe_db_filename = 'recipe_dataset.csv' #TODO: Change dummy filename for the real dataset filename

PandasDataFrame = TypeVar('pandas.core.frame.DataFrame')

class RecipeParams(BaseModel):
    recipe_db: PandasDataFrame
    max_total_time: int
    diet_type: str
    constraints: Dict[str, float]

# @function_tool
def fetch_local_recipe(recipe_params: RecipeParams) -> str:
        """
        Fetch recipe from a local recipe dataset that meets user-specified parameters.
        """
        results = recipe_params.recipe_db.copy()
 
        # 1. Time Constraint
        results = results[results['total_time'] <= recipe_params.max_total_time]
 
        # 2. Vegetarian Guardrail
        if recipe_params.diet_type.lower() == "vegetarian":
            meat_keywords = ['chicken', 'beef', 'pork', 'fish', 'lamb', 'shrimp']
            results = results[~results['ingredients'].str.contains('|'.join(meat_keywords), case=False)]
 
        # 3. Nutrition Dictionary Mapping (Quantitative Logic)
        for key, limit in recipe_params.constraints.items():
            if key in ['sodium', 'carbohydrates', 'fat', 'calories']:
                results = results[results['nutrition'].apply(lambda x: x.get(key, 0) <= limit)]
 
        if results.empty:
            return "NO_MATCH: No local recipes meet these strict Canadian health criteria."
 
        # Sort by rating and return top result
        match = results.sort_values(by='rating', ascending=False).head(1)
        return match.to_json()


# TODO: Fix the implementation logic in modify_recipe
# @function_tool
def modify_recipe(recipe_json: str, target_reduction: str, num_serving: int) -> str:
        """
        Tool: Logic to adapt ingredient quantities based on Canada Food Guide.
        """
        recipe = json.loads(recipe_json)[0]

        updated_ingredients = []

        # First adjust the ingredients for the desired number of servings
        pass

        # TODO: Modify the code below for more accurate adjustments
        # Then, reduce salt/sugar quantities found in the ingredients list
        for ing in recipe['ingredients']:
            if "salt" in ing.lower() or "sugar" in ing.lower():
                updated_ingredients.append(f"{ing} (REDUCED BY 50% per Health Canada guidelines)")
            else:
                updated_ingredients.append(ing)
        recipe['ingredients'] = updated_ingredients
        return json.dumps(recipe)


@dataclass
class FoodPlanner(Agent):
    """Food planning agent. """

    def __post_init__(self):
        """Set the set of tools for the agent."""

        # Set the gemini grounding tool with the default model
        gemini_grounding_tool = GeminiGroundingWithGoogleSearch()

        # Add the gemini ground tool
        self.tools.append(function_tool(
                gemini_grounding_tool.get_web_search_grounded_response,
                name_override="search_web",
            ))
        
        # Add the fetch recipe tool
        self.tools.append(fetch_local_recipe)
        # self.tools.append(modify_recipe)
        