from viktor import InitialEntity

from .project.controller import Controller as Project
from .project_folder.controller import Controller

initial_entities = [
    InitialEntity(
        "Controller",
        name="Projects",
        children=[
            InitialEntity(
                "Project",
                name="Example Project",
            )
        ],
    )
]
