import json
import re
import requests
from core.config import settings

def _get_raw_github_url(github_url: str) -> str | None:
    """Convert a GitHub blob URL to a raw.githubusercontent.com URL.
    Returns None if it's not a file link (e.g. repo root).
    """
    github_url = github_url.strip()
    
    # If already a raw URL, return as is
    if github_url.startswith("https://raw.githubusercontent.com/"):
        return github_url
        
    # Convert github.com blob URLs
    # Example: https://github.com/user/repo/blob/main/Solution.java
    # To:      https://raw.githubusercontent.com/user/repo/main/Solution.java
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$", github_url)
    if match:
        user, repo, branch, path = match.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
        
    return None

def fetch_github_code(github_url: str) -> str:
    """Fetch the raw source code from a GitHub URL."""
    raw_url = _get_raw_github_url(github_url)
    if not raw_url:
        raise ValueError("AI analysis is only available for single file submissions (e.g., a direct link to a file like Solution.java). Please ensure the student submitted a file link.")
        
    try:
        response = requests.get(raw_url, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        raise ValueError(f"Failed to fetch code from GitHub: {str(e)}")

def analyze_code_with_sarvam(practical_title: str, practical_desc: str, code_content: str) -> dict:
    """Analyze the given code using Sarvam AI and return remarks and suggestions.
    
    Returns a dict with 'remarks' and 'suggestions' keys.
    """
    if not settings.sarvam_api_key:
        raise ValueError("SARVAM_API_KEY is not configured in environment variables.")

    try:
        # We wrap the import to fail gracefully if the package isn't installed
        from sarvamai import SarvamAI
    except ImportError:
        raise ValueError("The 'sarvamai' package is not installed. Please install it to use this feature.")

    client = SarvamAI(api_subscription_key=settings.sarvam_api_key)

    system_prompt = (
        "You are a strict but helpful computer science professor evaluating a student's practical submission.\n"
        "You will be given the Practical Title, Practical Description, and the Student's Code.\n"
        "Your task is to analyze the code based on the practical requirements and provide feedback.\n"
        "Provide your evaluation strictly as a JSON object with three keys:\n"
        "1. 'remarks': A concise summary of the code's correctness, efficiency, and adherence to requirements. MAXIMUM 2 SENTENCES. Write this DIRECTLY to the student (e.g., 'Your code successfully...', 'You handled X well...').\n"
        "2. 'suggestions': Actionable advice for the student to improve their code quality, logic, or edge-case handling. MAXIMUM 2 SENTENCES. Write this DIRECTLY to the student (e.g., 'Consider using a set for...', 'Make sure you handle...').\n"
        "3. 'viva_questions': A list of 2 to 3 short, specific questions (array of strings) that a professor could ask the student to test their understanding of the code they wrote.\n"
        "Do not include markdown blocks like ```json in the final response, just return the raw JSON."
    )

    user_prompt = f"""
        Practical Title: {practical_title}

        Practical Description:
        {practical_desc}

        Student's Code:
        {code_content}
        """

    try:
        response = client.chat.completions(
            model="sarvam-105b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        
        output_text = response.choices[0].message.content.strip()
        
        # Clean up any markdown json formatting or conversational padding
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.startswith("```"):
            output_text = output_text[3:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]
            
        output_text = output_text.strip()
        
        # In case the model outputs conversational text before or after the JSON
        match = re.search(r"(\{.*\})", output_text, re.DOTALL)
        if match:
            output_text = match.group(1)
            
        parsed_result = json.loads(output_text)
        
        remarks = parsed_result.get("remarks", "No remarks provided by AI.")
        if isinstance(remarks, list):
            remarks = " ".join(remarks)
            
        suggestions = parsed_result.get("suggestions", "No suggestions provided by AI.")
        if isinstance(suggestions, list):
            suggestions = "\n".join(f"• {s}" for s in suggestions)

        return {
            "remarks": remarks,
            "suggestions": suggestions,
            "viva_questions": parsed_result.get("viva_questions", [])
        }
        
    except Exception as e:
        raise ValueError(f"Failed to analyze code with Sarvam AI: {str(e)}")
