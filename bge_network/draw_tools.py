from bge import render
from itertools import product, tee
from math import radians, sin, cos, pi, asin
from mathutils import Vector, Euler


def draw_arrow(point, orientation, length=1.5, branch_length=0.4, angle=30, colour=[1, 0, 0]):
    left = Vector((sin(radians(angle)), -cos(radians(angle)), 0))
    right = Vector((-sin(radians(angle)), -cos(radians(angle)), 0))

    left.rotate(orientation)
    right.rotate(orientation)

    left.length = branch_length
    right.length = branch_length

    direction = Vector((0, 1, 0)) * length
    direction.rotate(orientation)

    render.drawLine(point, point + direction, colour)
    render.drawLine(point + direction, point + direction + right, colour)
    render.drawLine(point + direction, point + direction + left, colour)


def draw_circle(point, orientation, size, fraction=1.0, steps=36, colour=[1, 0, 0]):
    last_point = None
    shift = (2 * pi / steps)

    for step in range(int(steps / fraction) + 1):
        index = step * shift
        x = sin(index) * size
        z = cos(index) * size
        step_point = Vector((x, 0, z))

        step_point.rotate(orientation)
        step_point += point

        if last_point:
            render.drawLine(last_point, step_point, colour)

        last_point = step_point


def draw_square_pyramid(point, orientation, angle=45, depth=1, colour=[1, 1, 1], pyramid=True, incline=True):
    points = []
    axis_values = [-1, 1]

    hypotenuse = depth if depth else 1
    angle = radians(angle)

    for x in axis_values:
        for z in axis_values:
            x_coordinate = hypotenuse * sin(angle) * x
            if incline:
                z += 1
            z_coordinate = hypotenuse * cos(angle) * z
            point_a = Vector((x_coordinate, depth, z_coordinate))
            points.append(point_a)

    for point_a in points:
        for point_b in points:

            if point_a is point_b:
                continue

            same_axis = [True for i in range(3) if point_a[i] == point_b[i]]

            if len(same_axis) < 2:
                continue

            a = point_a.copy()
            a.rotate(orientation)

            b = point_b.copy()
            b.rotate(orientation)

            render.drawLine(a + point, b + point, colour)

            if pyramid:
                render.drawLine(point, b + point, colour)


def draw_plane(point, orientation, size=1, offset=0, colour=[1, 1, 1]):
    angle = asin(size)
    draw_square_pyramid(point, orientation, angle, offset, colour, False)


def draw_box(point, orientation, width=1, height=1, length=1, colour=[1, 1, 1]):
    axis_values = [-.5, .5]

    points = [Vector((x * width, y * length, z * height)) for x, y, z in product(*tee(axis_values, 3))]

    for point_a in points:
        for point_b in points:
            if point_a is point_b:
                continue

            if len(set(point_a).intersection(point_b)) != 2:
                continue

            a = point_a.copy()
            b = point_b.copy()

            a.rotate(orientation)
            b.rotate(orientation)

            render.drawLine(a + point, b + point, colour)
