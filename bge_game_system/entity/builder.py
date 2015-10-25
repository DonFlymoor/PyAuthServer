from game_system.entity import MeshComponent, TransformComponent, AnimationComponent, PhysicsComponent, \
    CameraComponent, EntityBuilderBase

from . import instance_components


class EntityBuilder(EntityBuilderBase):
    component_classes = {}

    def __init__(self, bge_scene, input_manager, empty_name="Empty", camera_name="Camera"):
        self.entity_to_game_obj = {}

        self._empty_name = empty_name
        self._input_manager = input_manager

        self._bge_scene = bge_scene
        self._camera_name = camera_name

    def load_entity(self, entity):
        object_name = None

        for component_name, component in entity.components.items():
            if isinstance(component, MeshComponent):
                object_name = component.mesh_name

            elif isinstance(component, CameraComponent):
                object_name = self._camera_name

        if object_name is None:
            object_name = self._empty_name

        existing_obj = self._bge_scene.objectsInactive[object_name]
        obj = self._bge_scene.addObject(object_name, object_name)

        # Prevent double scaling
        obj.worldTransform = existing_obj.worldTransform.inverted() * obj.worldTransform

        self.entity_to_game_obj[entity] = obj
        super().load_entity(entity)

    def unload_entity(self, entity):
        obj = self.entity_to_game_obj.pop(entity)
        super().unload_entity(entity)
        obj.endObject()

    def create_component(self, entity, class_component, component_cls):
        obj = self.entity_to_game_obj[entity]
        component = component_cls(entity, obj, class_component)

        return component


EntityBuilder.register_class(TransformComponent, instance_components.TransformInstanceComponent)
EntityBuilder.register_class(MeshComponent, instance_components.MeshInstanceComponent)
EntityBuilder.register_class(AnimationComponent, instance_components.AnimationInstanceComponent)
EntityBuilder.register_class(PhysicsComponent, instance_components.PhysicsInstanceComponent)
