"""Copyright (c) 2023 VIKTOR B.V.
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.
VIKTOR B.V. PROVIDES THIS SOFTWARE ON AN "AS IS" BASIS, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT
SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import pickle

from viktor import UserError
from viktor import ViktorController
from viktor.core import Storage
from viktor.core import File
from viktor.views import WebResult
from viktor.views import WebView
from tempfile import NamedTemporaryFile
import ifcopenshell
from ifcopenshell.util import element as Element
from openai import OpenAI, AssistantEventHandler
from openai.types.beta.threads import ImageFileContentBlock
import json
from app.project.parametrization import Parametrization
from typing_extensions import override


from ..AI_search.chat_view import generate_html_code
from ..AI_search.retrieval_assistant import RetrievalAssistant
from pathlib import Path
from viktor.views import IFCView, IFCResult


def get_object_data_by_classes(file, class_type):
    objects_data = []
    objects = file.by_type(class_type)
    for obj in objects:
        obj_id = obj.id()
        objects_data.append({
            'ExpressID': obj.id(),
            'GlobalId': obj.GlobalId,
            'Class': obj.is_a(),
            'PredefinedType': Element.get_predefined_type(obj),
            'Name': obj.Name,
            'Level': Element.get_container(obj).Name
            if Element.get_container(obj)
            else '',
            'ObjectType': Element.get_type(obj).Name
            if Element.get_type(obj)
            else '',
            'QuantitySets': Element.get_psets(obj, qtos_only=True),
            'PropertySets': Element.get_psets(obj, psets_only=True),
        })
    return objects_data

class Controller(ViktorController):
    """Controller class for Document searcher app"""

    label = "Documents"
    parametrization = Parametrization
    client = None
    assistant = None
    thread = None

    def push_model(self, params, **kwargs):
        """Function to push the uploaded IFC file to the Storage"""
        if not params.input.ifcfile:
            raise UserError("Please upload an IFC file first.")

        temp_f = NamedTemporaryFile(suffix=".ifc", delete=False, mode="w")
        temp_f.write(params.input.ifcfile.file.getvalue())
        model = ifcopenshell.open(Path(temp_f.name))
        data = get_object_data_by_classes(model, "IfcBuildingElement")
        with open('temp/model_data.json', 'w+') as fp:
            json.dump(data, fp)

        self.create_ai_assistant()

    def create_ai_assistant(self):

        client = OpenAI(api_key=API_KEY)

        assistant = client.beta.assistants.create(
        instructions="You are an AI assistant that will allow users to talk to their data-rich models. In the responses where you're running code, do not provide any additional response besides the code and the output of the code. I will provide you with json files that contain data that were either made from Speckle or from IfcOpenShell and you need to be able to read this data and perform analysis on it.",
        model="gpt-4o-2024-05-13",
        tools=[{"type": "code_interpreter"}]
        )

        file = client.files.create(
            file=open("temp/model_data.json", "rb"),
            purpose='assistants'
        )

        thread = client.beta.threads.create(
            messages=[
                {
                    "role": "user",
                    "content": "Here's some data in json format. It contains some data about a building, either structural, architectural or MEP-related.",
                    "attachments": [
                        {
                        "file_id": file.id,
                        "tools": [{"type": "code_interpreter"}]
                        }
                    ]
                }
            ]
        )

        Storage().set(key="assistant_id", data=File.from_data(assistant.id), scope='entity')
        Storage().set(key="thread_id", data=File.from_data(thread.id), scope='entity')

    class EventHandler(AssistantEventHandler):    
        @override
        def on_text_created(self, text) -> None:
            print(f"\nassistant > ", end="", flush=True)
            
        @override
        def on_text_delta(self, delta, snapshot):
            print(delta.value, end="", flush=True)
            
        def on_tool_call_created(self, tool_call):
            print(f"\nassistant > {tool_call.type}\n", flush=True)
        
        def on_tool_call_delta(self, delta, snapshot):
            if delta.type == 'code_interpreter':
                if delta.code_interpreter.input:
                    print(delta.code_interpreter.input, end="", flush=True)
            if delta.code_interpreter.outputs:
                print(f"\n\noutput >", flush=True)
                for output in delta.code_interpreter.outputs:
                    if output.type == "logs":
                        print(f"\n{output.logs}", flush=True)
        
        # Then, we use the `stream` SDK helper 
        # with the `EventHandler` class to create the Run 
        # and stream the response.
        
    @WebView("Conversation", duration_guess=5)
    def conversation(self, params, **kwargs):
        """View for showing the questions, answers and sources to the user."""
        client = OpenAI(api_key=API_KEY)
        assistant_id = Storage().get(key="assistant_id", scope="entity").getvalue()
        thread_id = Storage().get(key="thread_id", scope="entity").getvalue()

        assistant = client.beta.assistants.retrieve(assistant_id=assistant_id)
        thread = client.beta.threads.retrieve(thread_id=thread_id)

        prompt = params.input.question

        with client.beta.threads.runs.stream(
            thread_id=thread.id,
            assistant_id=assistant.id,
            instructions=prompt,
            event_handler=self.EventHandler(),
        ) as stream:
            stream.until_done()

        messages = client.beta.threads.messages.list(thread_id=thread.id)

        new_file_ids = []
        new_messages = []
        for msg in messages.data:
            for cont in msg.content:
                if isinstance(cont, ImageFileContentBlock):
                    new_file_ids.append(cont.image_file.file_id)
                else:
                    new_messages.append(cont.text.value)
        
        for i, file_id in enumerate(new_file_ids):
            img_response = client.files.with_raw_response.retrieve_content(file_id)
            content = img_response.content
            with open(f'temp/image_{i}.png', 'wb') as f:
                f.write(content)

        answer = "\n".join(new_messages)

        html = generate_html_code(
            params.input.question, answer#, retrieval_assistant.metadata_list, retrieval_assistant.context_list
        )
        return WebResult(html=html)

    @IFCView("IFC view", duration_guess=1)
    def get_ifc_view(self, params, **kwargs):
        return IFCResult(params.input.ifcfile)
