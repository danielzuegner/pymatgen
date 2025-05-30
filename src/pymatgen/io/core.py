"""
This module defines the abstract interface for reading and writing calculation
inputs in pymatgen. The interface comprises a 3-tiered hierarchy of classes.

1. An InputFile object represents the contents of a single input file, e.g.
   the INCAR. This class standardizes file read and write operations.
2. An InputSet is a dict-like container that maps filenames (keys) to file
   contents (either strings or InputFile objects). This class provides a standard
   write_input() method.
3. InputGenerator classes implement a get_input_set method that, when provided
   with a structure, return an InputSet object with all parameters set correctly.
   Calculation input files can be written to disk with the write_inputs method.

If you want to implement a new InputGenerator, please take note of the following:

1. You must implement a get_input_set method that returns an InputSet
2. All customization of calculation parameters should be done in the __init__
   method of the InputGenerator. The idea is that the generator contains
   the "recipe", but nothing that is specific to a particular system. get_input_set
   takes system-specific information (such as structure) and applies the recipe.
3. All InputGenerator must save all supplied args and kwargs as instance variables.
   e.g. self.my_arg = my_arg and self.kwargs = kwargs in the __init__. This
   ensures the as_dict and from_dict work correctly.
"""

from __future__ import annotations

import abc
import copy
import os
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZipFile

from monty.io import zopen
from monty.json import MSONable

if TYPE_CHECKING:
    from typing_extensions import Self

    from pymatgen.util.typing import PathLike


__author__ = "Ryan Kingsbury"
__email__ = "RKingsbury@lbl.gov"
__status__ = "Development"
__date__ = "October 2021"


class InputFile(MSONable):
    """
    Abstract base class to represent a single input file. Note that use of this class
    is optional; it is possible create an InputSet that does not rely on underlying
    InputFile objects.

    All InputFile classes must implement a get_str method, which is called by
    write_file.

    If InputFile classes implement an __init__ method, they must assign all arguments
    to __init__ as attributes.
    """

    def __str__(self) -> str:
        return self.get_str()

    @abc.abstractmethod
    def get_str(self) -> str:
        """Return a string representation of an entire input file."""

    def write_file(self, filename: PathLike) -> None:
        """Write the input file.

        Args:
            filename: The filename to output to, including path.
        """
        with zopen(Path(filename), mode="wt", encoding="utf-8") as file:
            file.write(self.get_str())  # type:ignore[arg-type]

    @classmethod
    @abc.abstractmethod
    def from_str(cls, contents: str) -> None:
        """
        Create an InputFile object from a string.

        Args:
            contents: The contents of the file as a single string

        Returns:
            InputFile
        """
        raise NotImplementedError(f"from_str has not been implemented in {cls.__name__}")

    @classmethod
    def from_file(cls, path: PathLike) -> None:
        """
        Creates an InputFile object from a file.

        Args:
            path: Filename to read, including path.

        Returns:
            InputFile
        """
        with zopen(Path(path), mode="rt", encoding="utf-8") as file:
            return cls.from_str(file.read())  # type:ignore[arg-type]


class InputSet(MSONable, MutableMapping):
    """
    Abstract base class for all InputSet classes. InputSet are dict-like
    containers for all calculation input data.

    Since InputSet inherits dict, it can be instantiated in the same manner,
    or a custom __init__ can be provided. Either way, `self` should be
    populated with keys that are filenames to be written, and values that are
    InputFile objects or strings representing the entire contents of the file.

    All InputSet must implement from_directory. Implementing the validate method
    is optional.
    """

    def __init__(self, inputs: dict[PathLike, str | InputFile] | None = None, **kwargs) -> None:
        """Instantiate an InputSet.

        Args:
            inputs: The core mapping of filename: file contents that defines the InputSet data.
                This should be a dict where keys are filenames and values are InputFile objects
                or strings representing the entire contents of the file. If a value is not an
                InputFile object nor a str, but has a __str__ method, this str representation
                of the object will be written to the corresponding file. This mapping will
                become the .inputs attribute of the InputSet.
            **kwargs: Any kwargs passed will be set as class attributes e.g.
                InputSet(inputs={}, foo='bar') will make InputSet.foo == 'bar'.
        """
        self.inputs = inputs or {}
        self._kwargs = kwargs
        self.__dict__.update(**kwargs)

    def __getattr__(self, key):
        """Allow accessing keys as attributes."""
        if key in self._kwargs:
            return self.get(key)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute {key!r}")

    def __copy__(self) -> Self:
        cls = type(self)
        new_instance = cls.__new__(cls)

        for key, val in self.__dict__.items():
            setattr(new_instance, key, val)

        return new_instance

    def __deepcopy__(self, memo: dict[int, InputSet]) -> Self:
        cls = type(self)
        new_instance = cls.__new__(cls)
        memo[id(self)] = new_instance

        for key, val in self.__dict__.items():
            setattr(new_instance, key, copy.deepcopy(val, memo))

        return new_instance

    def __len__(self) -> int:
        return len(self.inputs)

    def __iter__(self) -> Iterator[PathLike]:
        return iter(self.inputs)

    def __getitem__(self, key: PathLike) -> str | InputFile | slice:
        return self.inputs[key]

    def __setitem__(self, key: PathLike, value: str | InputFile) -> None:
        self.inputs[key] = value

    def __delitem__(self, key: PathLike) -> None:
        del self.inputs[key]

    def __or__(self, other: dict | Self) -> Self:
        """Enable dict merge operator |."""
        if isinstance(other, dict):
            other = type(self)(other)
        if not isinstance(other, type(self)):
            return NotImplemented
        return type(self)({**self.inputs, **other.inputs}, **self._kwargs)

    def write_input(
        self,
        directory: PathLike,
        make_dir: bool = True,
        overwrite: bool = True,
        zip_inputs: bool = False,
    ) -> None:
        """Write inputs to one or more files.

        Args:
            directory: Directory to write input files to
            make_dir: Whether to create the directory if it does not already exist.
            overwrite: Whether to overwrite an input file if it already exists.
            Additional kwargs are passed to generate_inputs
            zip_inputs: If True, inputs will be zipped into a file with the
                same name as the InputSet (e.g., InputSet.zip)
        """
        path = directory if isinstance(directory, Path) else Path(directory)

        for fname, contents in self.inputs.items():
            file_path = path / fname

            if not path.exists() and make_dir:
                path.mkdir(parents=True, exist_ok=True)

            if file_path.exists() and not overwrite:
                raise FileExistsError(fname)
            file_path.touch()

            # write the file
            if isinstance(contents, InputFile):
                contents.write_file(file_path)
            else:
                with zopen(file_path, mode="wt", encoding="utf-8") as file:
                    file.write(str(contents))  # type:ignore[arg-type]

        if zip_inputs:
            filename = path / f"{type(self).__name__}.zip"
            with ZipFile(filename, mode="w") as zip_file:
                for fname in self.inputs:
                    file_path = path / fname
                    try:
                        zip_file.write(file_path)
                        os.remove(file_path)
                    except FileNotFoundError:
                        pass

    @classmethod
    def from_directory(cls, directory: PathLike) -> None:
        """
        Construct an InputSet from a directory of one or more files.

        Args:
            directory: Directory to read input files from
        """
        raise NotImplementedError(f"from_directory has not been implemented in {cls.__name__}")

    def validate(self) -> bool:
        """
        A place to implement basic checks to verify the validity of an
        input set. Can be as simple or as complex as desired.

        Will raise a NotImplementedError unless overloaded by the inheriting class.
        """
        raise NotImplementedError(f".validate() has not been implemented in {type(self).__name__}")


class InputGenerator(MSONable):
    """
    InputGenerator classes serve as generators for Input objects. They contain
    settings or sets of instructions for how to create Input from a set of
    coordinates or a previous calculation directory.
    """

    @abc.abstractmethod
    def get_input_set(self, *args, **kwargs):
        """Generate an InputSet object. Typically the first argument to this method
        will be a Structure or other form of atomic coordinates.
        """
        raise NotImplementedError(f"get_input_set has not been implemented in {type(self).__name__}")


class ParseError(SyntaxError):
    """Indicate a problem was encountered during parsing due to unexpected formatting."""
