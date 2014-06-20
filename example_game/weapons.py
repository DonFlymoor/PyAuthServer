from game_system.weapons import ProjectileWeapon

from mathutils import Vector

from .actors import ArrowProjectile, BowAttachment
from .signals import UIWeaponDataChangedSignal

__all__ = ["BowWeapon"]


class BowWeapon(ProjectileWeapon):

    def on_notify(self, name):
        # This way ammo is still updated locally
        if name == "ammo":
            UIWeaponDataChangedSignal.invoke("ammo", self.ammo)

        else:
            super().on_notify(name)

    def on_initialised(self):
        super().on_initialised()

        self.max_ammo = self.ammo = 100
        self.attachment_class = BowAttachment

        self.shoot_interval = 0.3
        self.theme_colour = [0.0, 0.50, 0.93, 1.0]

        self.projectile_class = ArrowProjectile
        self.projectile_velocity = Vector((0, 50, 0))
